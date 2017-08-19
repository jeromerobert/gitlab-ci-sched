# Installing

## System wide (need root)

`./setup.py install`


## User wide

`./setup.py install --user`


## Virtuaenv

```
virtualenv env
. env/bin/activate
./setup.py install
```

# Using

Create a file named `gitlab-ci-sched.yml` with content like:

```yaml
server:
  url: https://gitlab.com
  token: XXXXXXXXX
dag:
  jeromerobert/project1/master:
  jeromerobert/project2/master:
  - jeromerobert/project1/master
  jeromerobert/foo/master:
  - jeromerobert/project2/master
  jeromerobert/bar/master:
  - jeromerobert/foo/master
  jeromerobert/bar/master:
  - jeromerobert/foo/master
we_only: testfull.*
email:bob@zboub.com
```

then run `gitlab-ci-sched`

You should also set all your job as [`except: pushes`](https://docs.gitlab.com/ce/ci/yaml/#only-and-except)
to avoid doublon in pipeline creation.

To use the `we_only` set your jobs as `when: manual` and they'll be
run only during on saturday or sunday.

email: the mail adress given will be passed to the jobs as trigger vairable 'GITLAB_USER_EMAIL'