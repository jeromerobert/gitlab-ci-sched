#! /usr/bin/env python

from setuptools import setup, find_packages
setup(
    name="gitlabci_sched",
    version="0.2.1",
    packages=['gitlabci_sched'],
    entry_points = {
        'console_scripts': ['gitlab-ci-sched=gitlabci_sched:main'],
    },
    install_requires=['python-gitlab>=1.1.0', 'py-dag', 'PyYAML', 'python-dateutil'],
    author="Jerome Robert",
    author_email="jeromerobert@gmx.com",
    description="This is a scheduler for Gitlab-CI jobs with inter projects dependencies",
    license="GPL",
    keywords="Gitlab CI",
    url="http://github/jeromerobert/gitlab-ci-sched",
)
