# Installing

## System wide (need root)

`pip install .`


## User wide

`pip install --user .`


## Virtualenv

```
virtualenv env
. env/bin/activate
pip install .
```

# Using

Create a file named `gitlab-ci-sched.yml` with content like:

```yaml
server:
  url: https://gitlab.com
  token: XXXXXXXXX
jobs:
  # jobs regexp (default is build.*)
  includes: build.*_.*
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
email: bob@zboub.com
```

then run `gitlab-ci-sched`

You should also set all your job as [`except: pushes`](https://docs.gitlab.com/ce/ci/yaml/#only-and-except)
to avoid doublon in pipeline creation.

To use the `we_only` option set your jobs as `when: manual` and they'll be
run only during on saturday or sunday.

The `email` value will be passed to the jobs as the `GITLAB_USER_EMAIL` trigger variable.
