from setuptools import setup

with open("README.md") as f:
    long_description = f.read()

setup_kwargs = dict(
    name="sentry_dramatiq",
    version="0.3.3",
    description="Dramatiq task processor integration for the Sentry SDK",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=["sentry_dramatiq"],
    author="Jacob Magnusson",
    author_email="m@jacobian.se",
    url="https://github.com/jmagnusson/sentry-dramatiq",
    license="BSD",
    platforms="any",
    install_requires=[
        "dramatiq>=1.9",
        "sentry_sdk",
    ],
    extras_require={
        "test": {
            "coverage",
            "flake8",
            "isort",
            "pytest",
            "black",
        },
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
    ],
)

if __name__ == "__main__":
    setup(**setup_kwargs)
