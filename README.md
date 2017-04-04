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
```

then run `gitlab-ci-sched`

You should also:
* Set the root builds of your pipeline as [manual](https://docs.gitlab.com/ce/ci/yaml/#manual-actions)
* Disable [pipeline triggers](https://docs.gitlab.com/ce/ci/triggers/README.html)
