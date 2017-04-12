#! /usr/bin/env python

import yaml
import gitlab
import dag
import re
import dateutil.parser
import logging
import time


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
        self.gitlab = gitlab.Gitlab(gitlab_url, gitlab_token)
        # cache for project ids. If projects id changes the deamon must be restarted
        self.project_ids = {}
        # Projects which must not be built
        self.locked_projects = set()
        self.finished_at = {}

    def _fill_dag(self):
        """ Build the DAG. Each node is a tuple like ('group/project', 'branch') """
        pass

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
        project_id = self.project_ids.get(project[0])
        if project_id is None:
            project_id = self.gitlab.projects.get(project[0]).id
            self.project_ids[project[0]] = project_id
        commit_id = self.gitlab.project_branches.get(project_id=project_id, id=project[1]).commit['id']
        r = self.gitlab.project_commit_statuses.list(project_id=project_id, commit_id=commit_id)
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
    def __strip_old_status(statuses):
        """
        Return only the most recent statuses
        """
        by_job = {}
        for s in statuses:
            l = by_job.get(s.name)
            if l is None:
                l = []
                by_job[s.name] = l
            l.append(s)
        result = []
        for job, s in by_job.iteritems():
            result.append(sorted(s, None, lambda x: x.created_at)[-1])
        return result

    def __build_global_status(self, project):
        """
        Possible status are pending, running, success, failed, canceled, skipped. The logic is:
        -* one build pending or running => lock child
        - one build manual or skipped => run & lock child
        - one canceled => do nothing
        - all build success, failed => store finished_at, if started_at < parent.finished_at then build
        """
        statuses = self.__strip_old_status(self.__raw_project_status(project))
        # Look only at build jobs
        statuses = self._filter_statuses(statuses)
        if self.__have_status(statuses, ['pending', 'running']):
            return self.LOCK_ONLY, statuses
        elif self.__have_status(statuses, ['skipped', 'manual']):
            return self.RUN, statuses
        elif self.__have_status(statuses, ['canceled']):
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
        return sorted(statuses, None, lambda x: x.finished_at)[-1].finished_at

    @staticmethod
    def __first_started_at(statuses):
        return sorted(statuses, None, lambda x: x.started_at)[0].started_at

    @staticmethod
    def __first_created_at(statuses):
        return sorted(statuses, None, lambda x: x.created_at)[0].created_at

    def __run_jobs(self, project, statuses):
        """ Run manual jobs """
        p_id = self.project_ids[project]
        for s in statuses:
            if s.status in ['skipped', 'manual']:
                logging.info("Playing build %d of project %s" % (s.id, project))
                self.gitlab.project_builds.get(s.id, project_id=p_id).play()
            if s.status == 'canceled':
                logging.info("Retrying build %d of project %s" % (s.id, project))
                self.gitlab.project_builds.get(s.id, project_id=p_id).retry()

    def __run_new_pipeline(self, project):
        logging.info("Running new pipeline for %s " % "/".join(project))
        pipeline = self.gitlab.project_pipelines.create({'project_id': self.project_ids[project[0]], 'ref': project[1]})
        gs, statuses = self.__build_global_status(project)
        self.__lock_project(project)
        self.__run_jobs(project[0], statuses)

    def __retry_pipeline(self, project, statuses):
        """ Not used because Gitlab does not allow to retry a successful pipeline. Kept for the record. """
        p_id = self.project_ids[project]
        pipeline_id = self.gitlab.project_builds.get(statuses[0].id, project_id=p_id).pipeline['id']
        logging.info("Retrying pipeline %d" % pipeline_id)
        self.gitlab.project_pipelines.get(pipeline_id, project_id=p_id).retry()

    def __lock_project(self, project):
        """ Tag a project as not-to-build """
        self.locked_projects.add(project)
        self.locked_projects.update(self.dag.all_downstreams(project))

    def __last_parent_date(self, project):
        last_parent_date = None
        for pred in self.dag.predecessors(project):
            d = self.finished_at.get(pred, None)
            logging.debug("Predecessor " + repr(pred) + " ended at " + str(d))
            if last_parent_date is None:
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
                    logging.debug("Processing project " + repr(project))
                    gs, statuses = self.__build_global_status(project)
                    logging.debug("Status is " + gs)
                    if gs == self.LOCK_ONLY:
                        self.__lock_project(project)
                    elif gs == self.RUN:
                        self.__lock_project(project)
                        self.__run_jobs(project[0], statuses)
                    elif gs == self.CANCELED:
                        last_parent_date = self.__last_parent_date(project)
                        if last_parent_date is not None and self.__first_created_at(statuses) < last_parent_date:
                            self.__run_new_pipeline(project)
                    else:
                        self.finished_at[project] = self.__last_finished_at(statuses)
                        logging.debug("Finished at " + str(self.finished_at[project]))
                        last_parent_date = self.__last_parent_date(project)
                        if last_parent_date is not None and self.__first_started_at(statuses) < last_parent_date:
                            self.__run_new_pipeline(project)
            except gitlab.exceptions.GitlabConnectionError as e:
                logging.info(e.error_message)
                self.gitlab = gitlab.Gitlab(self.gitlab_url, self.gitlab_token)
            except gitlab.exceptions.GitlabError as e:
                logging.warning(e.error_message)
                self._wait_some_time()
                self._wait_some_time()
                self.gitlab = gitlab.Gitlab(self.gitlab_url, self.gitlab_token)
            self._wait_some_time()


class YamlScheduler(Scheduler):
    """ A scheduler setup from a YAML file """
    PROJECT_REGEX = re.compile('([^/]+/[^/]+)/(.+)')

    def __init__(self, yaml_file):
        with open(yaml_file) as f:
            self.config = yaml.load(f)
            Scheduler.__init__(self, self.config['server']['url'], self.config['server']['token'])

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
        """ Keep only the build statuses . We don't need to wait for the test jobs to run the next pipeline """
        return [x for x in statuses if 'build' in x.name and x.name != 'build-next']

    def _wait_some_time(self):
        time.sleep(30)


def main():
    logging.basicConfig(level=logging.INFO)
    YamlScheduler('gitlab-ci-sched.yml').run()


if __name__=='__main__':
    main()
