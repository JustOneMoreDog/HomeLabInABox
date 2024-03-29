#!/bin/bash

# Function to check for root privileges
check_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "ERROR: This script must be run as root."
    exit 1
  fi
}

# Function to install a package with error handling
install_package() {
  if command -v $1 &> /dev/null; then
    echo "$1 is already installed"
  else
    echo "Installing $1..."  
    if command -v apt-get &> /dev/null; then
      apt-get update -y && apt-get install -y $1  
    elif command -v yum &> /dev/null; then
      yum install -y $1
    else
      echo "ERROR: Unsupported package manager. Please install $1 manually."
      exit 2
    fi

    if [[ $? -ne 0 ]]; then
      echo "ERROR: Failed to install $1"
      exit 2
    fi
  fi
}

# Main execution
check_root

# Bootstrap check
read -p "Have you followed the Bootstrapping instructions in the README? (y/n) " -r
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
  echo "ERROR: Please complete the bootstrapping steps before running this script."
  exit 1
fi

# Install dependencies
install_package python3.11
install_package python3.11-venv 
install_package vim
install_package git
install_package yamllint


# Clone the HIAB repository 
if git clone https://github.com/JustOneMoreDog/HomeLabInABox.git -b Proxmox-Module; then
  cd HomeLabInABox
else
  echo "ERROR: Failed to clone HomeLabInABox repository."
  exit 3
fi

# Create and activate virtual environment
python3 -m venv env
source env/bin/activate 

# Install HIAB requirements
pip install -r requirements.txt 

python HomeLabInABox.py
