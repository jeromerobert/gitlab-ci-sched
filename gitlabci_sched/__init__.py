#! /usr/bin/env python

import yaml
import gitlab
import dag
import re
import dateutil.parser
import logging
import time
import datetime
import urllib
import requests

class Scheduler(object):
    """
    Use the commit status web API of Gitlab to scheduler build efficiently
    see https://docs.gitlab.com/ce/api/commits.html#get-the-status-of-a-commit
    """
    SUCCESS = 'SUCCESS'
    RUN = 'RUN'
    LOCK_ONLY = 'LOCK_ONLY'
    CANCELED = 'CANCELED'

    def __init__(self, gitlab_url, gitlab_token):
        self.dag = dag.DAG()
        self.gitlab_url = gitlab_url
        self.gitlab_token = gitlab_token
        self.gitlab = gitlab.Gitlab(gitlab_url, gitlab_token, api_version=4)
        # Cache group/project to Gitlab project objects. If projects
        # changes the deamon must be restarted
        self.projects = {}
        # cache for triggers
        self.triggers = {}
        # Projects which must not be built
        self.locked_projects = set()
        self.finished_at = {}

    def _fill_dag(self):
        """ Build the DAG. Each node is a tuple like ('group/project', 'branch') """
        pass

    def _can_run_manual(self, project, job):
        return False

    def _additional_variables(self):
        return {}

    def _filter_statuses(self, statuses):
        """
        Keep only the status which will be used to get the pipeline status
        See https://docs.gitlab.com/ce/api/commits.html#get-the-status-of-a-commit
        :param statuses:
        :return: a list of status
        """
        return statuses

    def _wait_some_time(self):
        pass

    def __raw_project_status(self, project):
        """
        Return the status of a given project branch as in
        https://docs.gitlab.com/ce/api/commits.html#get-the-status-of-a-commit
        :param project: a tuple ('group/project', 'branch')
        :return: And array of status
        """
        glproject = self.projects.get(project[0])
        if glproject is None:
            glproject = self.gitlab.projects.get(project[0])
            self.projects[project[0]] = glproject
        commit = glproject.branches.get(project[1]).commit
        if commit is None:
            # The branch does not exists or the repo is empty/broken
            return None
        r = glproject.commits.get(commit['id']).statuses.list(all=True)
        for s in r:
            try:
                s.created_at = dateutil.parser.parse(s.created_at)
            except TypeError:
                pass

            try:
                s.started_at = dateutil.parser.parse(s.started_at)
            except TypeError:
                pass

            try:
                s.finished_at = dateutil.parser.parse(s.finished_at)
            except TypeError:
                pass
        return r

    @staticmethod
    def __strip_old_status(statuses, branch):
        """
        Return only the most recent statuses for a given branch
        """
        by_job = {}
        for s in statuses:
            if s.ref == branch:
                l = by_job.get(s.name)
                if l is None:
                    l = []
                    by_job[s.name] = l
                l.append(s)
        result = []
        for job, s in by_job.items():
            result.append(sorted(s, key=lambda x: x.created_at)[-1])
        return result

    def __run_manual_jobs(self, project, statuses):
        p = self.projects[project[0]]
        for s in statuses:
            if s.status == 'manual' and self._can_run_manual(project, s.name):
                build = p.jobs.get(s.id)
                logging.info("Running manual job %d in project %s", s.id, project[0])
                build.play()

    def __build_global_status(self, project):

        """
        Possible status are created, pending, running, failed, success, canceled, skipped, manual. The logic is:
        - no build => build
        - one build pending, running => lock child
        - one build canceled, manual, skipped, failed => build if parent rebuilt
        - all build success => store finished_at, if started_at < parent.finished_at then build
        """
        rps = self.__raw_project_status(project)
        if rps is None:
            # The repository contains no commit
            return self.CANCELED, None
        statuses = self.__strip_old_status(rps, project[1])
        self.__run_manual_jobs(project, statuses)
        # Look only at build jobs
        statuses = self._filter_statuses(statuses)
        logging.info("Computing status from those build: "+" ".join([str(s.id) for s in statuses]))
        if len(statuses) == 0:
            return self.RUN, statuses
        elif self.__have_status(statuses, ['pending', 'running']):
            return self.LOCK_ONLY, statuses
        elif self.__have_status(statuses, ['canceled', 'skipped', 'manual', 'failed']):
            return self.CANCELED, statuses
        else:
            return self.SUCCESS, statuses

    @staticmethod
    def __have_status(statuses, labels):
        """ Return true if at least one status is in labels """
        for s in statuses:
            if s.status in labels:
                return True
        return False

    @staticmethod
    def __last_finished_at(statuses):
        if len(statuses) == 0:
            return None
        else:
            return sorted(statuses, key=lambda x: x.finished_at)[-1].finished_at

    @staticmethod
    def __first_started_at(statuses):
        return sorted(statuses, key=lambda x: x.started_at)[0].started_at

    @staticmethod
    def __first_created_at(statuses):
        return sorted(statuses, key=lambda x: x.created_at)[0].created_at

    def __get_trigger(self, glproject):
        """ Get or create a trigger """
        result = self.triggers.get(glproject.id)
        if result is None:
            for trigger in glproject.triggers.list():
                legacy = trigger.owner is None and trigger.description is None
                if not legacy:
                    result = trigger
                    break
            if result is None:
                result = glproject.triggers.create({'description': 'gitlab-ci-sched'})
            self.triggers[glproject.id] = result
        return result

    def __trigger_variables(self, project):
        """ Return variables for a trigger to describe the dependencies
            Should be built like the CI_COMMIT_REF_SLUG variable defined here:
            https://gitlab.com/help/ci/variables/README.md#predefined-variables-environment-variables """
        r = {}
        for p_name, branch in self.dag.predecessors(project):
            r['CI_REF_' + p_name.upper().replace('/', '_')] = re.sub('[^a-z0-9]', '-', branch.lower() )
        r.update(self._additional_variables())
        return r

    def __run_new_pipeline(self, project):
        logging.info("Running new pipeline for %s " % "/".join(project))
        self.__lock_project(project)
        p = self.projects[project[0]]
        token = self.__get_trigger(p).token
        p.trigger_pipeline(project[1], token, self.__trigger_variables(project))

    def __lock_project(self, project):
        """ Tag a project as not-to-build """
        self.locked_projects.add(project)
        self.locked_projects.update(self.dag.all_downstreams(project))

    def __last_parent_date(self, project):
        """
        Return the date of the most recent parent job.
        If a parent has not run yet, return None
        """
        last_parent_date = None
        for pred in self.dag.predecessors(project):
            d = self.finished_at.get(pred, None)
            logging.info("Predecessor " + repr(pred) + " ended at " + str(d))
            if d is None:
                # One predecessor as not run yet so no need to test others
                last_parent_date = None
                break
            elif last_parent_date is None:
                last_parent_date = d
            elif d > last_parent_date:
                last_parent_date = d
        return last_parent_date

    def run(self):
        """ Scheduling main loop """
        self._fill_dag()
        sorted_projects = self.dag.topological_sort()
        while True:
            try:
                self.finished_at.clear()
                self.locked_projects.clear()
                for project in sorted_projects:
                    if project in self.locked_projects:
                        continue
                    logging.info("Processing project " + repr(project))
                    gs, statuses = self.__build_global_status(project)
                    logging.debug("Status is " + gs)
                    if gs == self.LOCK_ONLY:
                        self.__lock_project(project)
                    elif gs == self.RUN:
                        self.__run_new_pipeline(project)
                    else:
                        if gs == self.SUCCESS:
                            self.finished_at[project] = self.__last_finished_at(statuses)
                            logging.debug("Finished at " + str(self.finished_at[project]))
                        last_parent_date = self.__last_parent_date(project)
                        if last_parent_date is not None and self.__first_created_at(statuses) < last_parent_date:
                            self.__run_new_pipeline(project)
            except gitlab.exceptions.GitlabConnectionError as e:
                logging.info(e.error_message)
                self.gitlab = gitlab.Gitlab(self.gitlab_url, self.gitlab_token)
            except gitlab.exceptions.GitlabError as e:
                logging.warning(e.error_message)
                self._wait_some_time()
                self._wait_some_time()
                self.gitlab = gitlab.Gitlab(self.gitlab_url, self.gitlab_token)
            # TODO: catching requests.exceptions.ConnectionError should be managed by python-gitlab
            # but it's not (python-gitlab bug)
            except requests.exceptions.RequestException as e:
                logging.warning(repr(e))
                self._wait_some_time()
                self._wait_some_time()
                self.gitlab = gitlab.Gitlab(self.gitlab_url, self.gitlab_token)
            self._wait_some_time()


class YamlScheduler(Scheduler):
    """ A scheduler setup from a YAML file """
    PROJECT_REGEX = re.compile('([^/]+/[^/]+)/(.+)')

    def __init__(self, yaml_file):
        with open(yaml_file) as f:
            self.config = yaml.safe_load(f)
            Scheduler.__init__(self, self.config['server']['url'], self.config['server']['token'])
        if 'we_only' in self.config:
            self.we_only = re.compile(self.config['we_only'])
        else:
            self.we_only = None
        if 'jobs' in self.config:
            self.job_includes = re.compile(self.config['jobs']['includes'])
        else:
            self.job_includes = re.compile(r'build.*')

    def __parse_project(self, name):
        m = self.PROJECT_REGEX.match(name)
        return m.group(1), m.group(2)

    def _fill_dag(self):
        """ Override Scheduler._fill_dag """
        for p in self.config['dag']:
            self.dag.add_node(self.__parse_project(p))

        for k, v in self.config['dag'].items():
            pk = self.__parse_project(k)
            if v is not None:
                for p in v:
                    self.dag.add_edge(self.__parse_project(p), pk)

        if not self.dag.validate():
            raise RuntimeError("Invalid dag")

    def _filter_statuses(self, statuses):
        return [x for x in statuses if self.job_includes.match(x.name)]

    def _wait_some_time(self):
        time.sleep(30)

    def _can_run_manual(self, project, job):
        return self.we_only is not None \
            and datetime.datetime.today().weekday() >= 5 \
            and self.we_only.match(job)

    def _additional_variables(self):
        if 'email' in self.config:
            return {'GITLAB_USER_EMAIL': self.config['email']}
        else:
            return {}

def main():
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s', datefmt='%m/%d/%Y %H:%M:%S')
    YamlScheduler('gitlab-ci-sched.yml').run()


if __name__=='__main__':
    main()
