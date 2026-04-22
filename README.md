# Heating System
This repo is for implementing various heating system models.
Install via:
    pip install git+https://github.com/petermads123/heatingsystem.git@main
or in dependecies in pyproject.toml:
    "heatingsystem @ git+https://github.com/petermads123/heatingsystem.git@main"

## PIController
A basic PI-controlled heating system


## Development
1. Have git installed on your machine.
2. Have python installed on your machine. At least the version specified in pyproject.toml
3. Have the python extension installed in your VSCode
4. CTRL + Shift + P -> Python: Create Environment -> Venv -> Select python version
5. Download dependencies by running "pip install -e." in the powershell terminal. Environment should activate automatically when powershell terminal is launched.

### Ruff
    For proper development, please have Ruff installed.
    After installement; go to File -> Preferences -> Settings.
        Turn on "Format on save".