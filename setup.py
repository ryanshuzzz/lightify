import os
from setuptools import setup
from setuptools.command.install import install
from lightify import __version__

README = open(os.path.join(os.path.dirname(__file__), 'README.rst')).read()

# allow setup.py to be run from any path
os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))


setup(
    name='lightify',
    version=__version__,
    packages=['lightify'],
    include_package_data=True,
    license='BSD License',
    description='A library to work with OSRAM lightify.',
    long_description=README,
    url='https://github.com/aneumeier/python-lightify',
    author='Andreas.Neumeier',
    author_email='andreas@neumeier.org',
    classifiers=[
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',    # example license
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3.4',
        'Topic :: Internet',
    ],
)
