from setuptools import setup

setup(
    name='lightify',
    version='1.0.7.2',
    packages=['lightify'],
    include_package_data=True,
    license='Apache License (2.0)',
    description='A library to work with OSRAM lightify.',
    long_description='A library to work with OSRAM lightify. Threadsafe.',
    url='https://github.com/tfriedel/python-lightify',
    author='Thomas Friedel',
    author_email='thomas.friedel@gmail.com',
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
