#!/bin/bash

set -e

declare PROJECT="$1"

if [ ! -d "/src/$PROJECT/" ]
then
    echo "Please clone $PROJECT into ../$PROJECT/"
    exit 1
fi

cd "/src/$PROJECT/"

if [[ -f perftest_requirements.txt ]]; then
    pip3 install -r perftest_requirements.txt
elif [[ -f functest_requirements.txt ]]; then
    pip3 install -r functest_requirements.txt
fi
