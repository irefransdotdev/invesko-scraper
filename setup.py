# Automatically created by: shub deploy
# (extended: find_packages() only covers packages/ - root-level modules
# must be listed in py_modules or they are missing from the deployed egg)

from setuptools import setup, find_packages

setup(
    name         = 'project',
    version      = '1.0',
    packages     = find_packages(exclude=('tests',)),
    py_modules   = [
        'config',
        'database',
        'items',
        'middlewares',
        'migrate_funds',
        'migrate_v2',
        'navpu_parsing',
        'pifa_parsing',
        'pse_edge_parsing',
        'pipelines',
        'reprocess_failed',
        'settings',
        'verify_db',
    ],
    entry_points = {'scrapy': ['settings = settings']},
)
