#!/bin/bash
# Change to the repo root, one up from this script, where RFbenchToolkit.py
# is. The launcher finds apps/icons/, apps/fonts/ and config/ from its own file, so
# nothing else depends on where it was started.
cd "$(dirname "$0")/.."
# Activate the Conda environment
source ~/miniconda3/bin/activate gnu
# Run app
python RFbenchToolkit.py
