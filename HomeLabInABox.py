import networkx as nx
import yaml
import os
import logging
import argparse
import sys
import subprocess
from AnsibleWrapper import AnsibleWrapper
import time
from rich import print
import random
import string
import crypt
import base64
import stat
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend


class ModuleConfigurationError(Exception):
    """Raised when a module configuration is invalid."""
    pass


class HomeLabInABox:
    def __init__(self, debug: bool = False):
        self.setup_logging(debug)
        self.desired_module_names = []
        self.desired_modules = []
        self.dependency_graph = nx.DiGraph()
        self.configuration = {}
        self.terraform_inventory = os.path.abspath(
            os.path.join(os.getcwd(), "inventory", "terraform_inventory.yaml")
        )
        self.roles_directory = os.path.abspath(os.path.join(os.getcwd(), "roles"))
        self.configuration_variables = {}
        self.all_modules = self.get_all_modules()

    def setup_logging(self, debug_mode: bool) -> None:
        """Sets up the logging for the project.
        
        Args: debug_mode (bool): If True, the logging level will be set to DEBUG and logs will also be printed to stdout. If False, the logging level will be set to WARNING and logs will only be printed to the file.
        
        Returns: None.
        """
        # Ansible sometimes produces warning messages so we want to always be capturing those
        logging_level = logging.DEBUG if debug_mode else logging.WARNING
        formatter = logging.Formatter('%(levelname)s:HIAB:%(message)s')

        logger = logging.getLogger()
        logger.setLevel(logging_level)

        # Create a file handler
        file_handler = logging.FileHandler(f"logs/{time.time()}.log")
        file_handler.setLevel(logging_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # Create a stream handler for stdout if we are debugging
        if debug_mode:
            stream_handler = logging.StreamHandler()
            stream_handler.setLevel(logging_level)
            stream_handler.setFormatter(formatter)
            logger.addHandler(stream_handler)

        logging.info("Logging setup complete")
        logging.warning("Logging setup complete")
        logging.debug("Logging setup complete")

    def generate_and_hash_password(self) -> str:
        """Generates a strong random password and returns its SHA512 hash.

        Returns: tuple: The generated password and its SHA512 hash.
        """
        characters = string.ascii_letters + string.digits
        salt = base64.urlsafe_b64encode(os.urandom(8)).decode()
        password = ''.join(random.choice(characters) for _ in range(12))
        self.plaintext_secret_to_file("root_password", password, "The root password for the hypervisor")
        password_hash = crypt.crypt(password, f"$6${salt}$")
        return password_hash
    
    def plaintext_secret_to_file(self, name: str, secret: any, description: str) -> None:
        """Writes a secret to our not so secure secrets.yaml file in the files directory.
        
        Args: 
            name (str): The name of the secret. 
            secret (any): The not so secret value. 
            description (str): A description of the secret.
        
        Returns: None
        """
        secrets_file_path = os.path.abspath(os.path.join("files", "secrets.yaml"))
        if os.path.exists(secrets_file_path):
            secrets_data = self.load_yaml(secrets_file_path)
        else:
            secrets_data = {"Secrets": []}
        secret_data = {
            "name": name,
            "secret": secret,
            "description": description
        }
        secrets_data['Secrets'].append(secret_data)
        self.save_yaml(secrets_file_path, secrets_data)
        logging.info(f"Secret '{name}' written to '{secrets_file_path}'")

    def generate_ansible_ssh_keypair(self) -> None:
        """Generates an SSH keypair for Ansible to use and saves it in the files directory.
        
        Returns: str: The public key string.
        """
        private_key_path = os.path.abspath(os.path.join("files", "ansible_id_rsa"))
        public_key_path = os.path.abspath(os.path.join("files", "ansible_id_rsa.pub"))
        if os.path.exists(private_key_path) and os.path.exists(public_key_path):
            logging.info("Ansible SSH keypair already exists")
            return
        logging.info("Generating Ansible SSH keypair")
        key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=4096,
            backend=default_backend()
        )
        private_key = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        )
        public_key = key.public_key().public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH
        )
        logging.info("Saving Ansible SSH keypair to files directory")
        with open(private_key_path, "wb") as f:
            f.write(private_key)
            # Equivalent to 600
            os.chmod(f.name, stat.S_IRUSR | stat.S_IWUSR)
        with open(public_key_path, "wb") as f:
            f.write(public_key)
            # Equivalent to 644
            os.chmod(f.name, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        public_key_string = public_key.decode('utf-8')
        return public_key_string

    def get_all_modules(self) -> list[dict]:
        """Gets all the modules from the Modules directory.
        
        Returns: list: All the module specs.
        """
        logging.info("Getting all module specs")
        specs = []
        for module in os.listdir("Modules"):
            module_path = os.path.join("Modules", module)
            if not os.path.isdir(module_path) or module == ".template":
                continue
            spec_path = os.path.join(module_path, "spec.yaml")
            spec = self.load_yaml(spec_path)
            spec["name"] = module
            specs.append(spec)
        return specs

    def load_yaml(self, yaml_path: str) -> dict:
        """Loads a YAML file from the given path and returns the data as a dictionary.

        Args: yaml_path (str): The path to the YAML file to load.

        Returns: dict: The data from the YAML file as a dictionary.
        """
        logging.info(f"Loading YAML file '{yaml_path}")
        try:
            with open(yaml_path, "r") as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"YAML file '{yaml_path}' not found.")
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"Invalid YAML in file '{yaml_path}': \n{e}")

    def save_yaml(self, filepath: str, data: dict) -> None:
        """Saves the given data to a YAML file at the given path and ensures keys are not sorted.

        Args: 
            filepath (str): The path to the YAML file to save. 
            data (dict): The data to save to the YAML file.
        
        Returns: None
        """
        if not isinstance(data, dict):
            raise TypeError(f"The 'data' argument must be a dictionary and not '{type(data)}'.")
        logging.info(f"Saving data to '{filepath}'")
        try:
            if not os.path.isabs(filepath):
                filepath = os.path.abspath(os.path.join(os.getcwd(), filepath))
            # Create any necessary directories in the path
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "w") as outfile:
                yaml.safe_dump(data, outfile, default_flow_style=False, sort_keys=False)
        except OSError as e:
            raise OSError(f"Error saving YAML file '{filepath}': {e}")
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"Error serializing data to YAML: {e}")

    def get_module_playbook(self, module: str) -> dict:
        """Gets the main playbook for the given module and injects our configuration variables into it.

        Args: module (str): The name of the module to get the playbook for.

        Returns: dict: The modified main playbook for the given module.
        """
        logging.info(f"Getting playbook for '{module}'")
        playbook_path = os.path.join("Modules", module, "playbook.yaml")
        playbook_data = self.load_yaml(playbook_path)
        # TO-DO: Handle the situation where the playbook already has vars defined (it really shouldn't though)
        for play in playbook_data:
            play["vars"] = self.configuration_variables
        return playbook_data

    def get_module_inventory(self, playbook: list[dict]) -> str:
        """Gets the inventory file for the given module. If the module's playbook targets localhost then we use a dummy /dev/null inventory file. Otherwise we use our dynamic inventory file powered by Terraform.
        
        Args: playbook (list): The playbook for the module.
        
        Returns: str: The inventory file for the module.
        """
        just_local_host = True
        for play in playbook:
            if play["hosts"] != "localhost":
                just_local_host = False
        if not just_local_host:
            return self.terraform_inventory
        return "/dev/null"

    def execute_ansible_playbooks(self, modules: list[str]) -> None:
        """Gets a module's playbook and inventory file and passes it to our Ansible wrapper which will execute it with the current working directory being the root directory of the project.
        
        Args: modules (list): The list of modules to execute playbooks for.
        
        Returns: None
        """
        for module in modules:
            playbook = self.get_module_playbook(module)
            inventory = self.get_module_inventory(playbook)
            ansible_runner = AnsibleWrapper(
                inventory=inventory,
                playbook=playbook,
                roles_directory=self.roles_directory,
                module_name=module
            )
            ansible_runner.run()

    def get_deployment_order(self) -> list:
        """Gets the deployment order for the modules based on the dependency graph.
        
        Returns: list: The deployment order for the modules.
        """
        logging.info("Getting deployment order")
        deployment_order = list(nx.topological_sort(self.dependency_graph))
        logging.info(f"Deployment order is: '{' -> '.join(deployment_order)}'")
        return deployment_order
    
    def link_module_roles(self, module_name: str) -> None:
        """Links the roles from the given module to the main roles directory so that they can be used across all modules.
        
        Args: module_name (str): The name of the module to link the roles from.

        Returns: None
        """
        logging.info(f"Linking '{module_name}' roles together")
        roles_dir = os.path.join("Modules", module_name, "roles")
        target_roles_dir = "roles"
        for role_name in os.listdir(roles_dir):
            role_dir_abs_path = os.path.abspath(os.path.join(roles_dir, role_name))
            if not os.path.isdir(role_dir_abs_path):
                logging.info(f"Skipping '{role_name}' since it is not a directory")
                continue
            source_role = os.path.abspath(os.path.join(roles_dir, role_name))
            target_role = os.path.abspath(os.path.join(target_roles_dir, role_name))
            if os.path.exists(target_role):
                # TO-DO: How do we handle modules that have duplicate named roles?
                logging.warning(f"Role '{role_name}' already exists in the main 'roles' directory.")
                continue
            logging.debug(f"Source: '{source_role}', Destination: '{target_role}'")
            os.symlink(src=source_role, dst=target_role, target_is_directory=True)
            logging.info(f"Linked role '{role_name}' from module '{module_name}'.")
    
    def check_for_cycles(self) -> None:
        """Checks the dependency graph for cycles and raises a ModuleConfigurationError with the cycles listed if any are found.
        
        Returns: None
        """
        if nx.is_directed_acyclic_graph(self.dependency_graph):
            return
        logging.warning("Cycle(s) detected")
        cycles_list = list(nx.simple_cycles(self.dependency_graph))
        cycles = "\n".join([f"  - {' -> '.join(cycle)}" for cycle in cycles_list])
        raise ModuleConfigurationError(f"The following cycles have been detected and operation cannot continue\n{cycles}")

    def add_module_dependencies_to_graph(self, module_dependencies: list, module_name: str) -> None:
        """Adds the dependencies for the given module to the dependency graph.
        
        Args: 
            module_dependencies (list): The list of dependencies for the module. 
            module_name (str): The name of the module to add to the graph.

        Returns: None
        """
        logging.info(f"Adding '{module_name}' to dependency graph")
        self.dependency_graph.add_node(module_name)
        for dependency in module_dependencies:
            logging.info(f"Adding '{dependency}' as a dependency for '{module_name}'")
            self.dependency_graph.add_edge(dependency, module_name)
            self.check_for_cycles()

    def add_module_dependencies_to_desired_modules(self, module_dependencies: list) -> None:
        """Adds the dependencies for the given module to the desired_module_names list.
        
        Args: 
            module_dependencies (list): The list of dependencies for the module. 
            module_name (str): The name of the module to add to the desired modules list.

        Returns: None
        """
        for dependency in module_dependencies:
            if dependency not in self.desired_module_names:
                logging.info(f"Adding '{dependency}' to desired module names list")
                self.desired_module_names.append(dependency)

    def gather_modules(self, refresh_available_modules: bool = False) -> None:
        """Gathers all available modules and writes them to a file. If the file already exists it will be overwritten.

        Args: refresh_available_modules (bool, optional): If True, the available modules will be refreshed. Defaults to False.

        Returns: None
        """
        logging.info("Gathering modules")
        if refresh_available_modules:
            modules = self.load_yaml("module_choices.yaml")
            modules["available_modules"] = []
        else:
            modules = {
                "wanted_modules": ["Put your desired modules here", "", ""],
                "available_modules": [],
            }
        for module in self.all_modules:
            modules["available_modules"].append(
                {"Name": module["name"], "Description": module["description"]}
            )
        self.save_yaml("module_choices.yaml", modules)

    def validate_modules(self) -> bool:
        """Validates that the user has selected valid modules from the available modules list.

        Returns: bool: True if the modules are valid, False if not.
        """
        logging.info("Validating modules")
        valid = True
        module_choices = self.get_desired_modules()
        for module in self.desired_module_names:
            # TO-DO: More robust checking for accidental user input
            if not module:
                continue
            a_valid_module_choice = any(x["name"] == module for x in self.all_modules)
            if not a_valid_module_choice:
                logging.warning(f"Invalid module choice '{module}' detected")
                valid = False
                module_choices["wanted_modules"][module] = module + " <--- invalid"
        if not valid:
            self.save_yaml("module_choices.yaml", module_choices)
        return valid

    def get_desired_modules(self) -> dict:
        """Gets the module choices from the module_choices.yaml file and puts the names of the chosen modules into a list.
        
        Returns: dict: The module choices from the module_choices.yaml file.
        """
        logging.info("Getting desired modules")
        self.desired_module_names = []
        module_choices = self.load_yaml("module_choices.yaml")
        for chosen_module in module_choices["wanted_modules"]:
            # TO-DO: More robust checking for accidental user input
            if not chosen_module:
                continue
            module = chosen_module.strip()
            self.desired_module_names.append(module)
        return module_choices

    def get_module_spec(self, target_module: str) -> dict:
        """Gets the spec for the desired module from the all_modules variable that was populated when the class was initialized.

        Args: target_module (str): The name of the module to get the spec for.

        Returns: dict: The spec for the desired module.
        """
        logging.info(f"Getting spec for '{target_module}'")
        for module in self.all_modules:
            if module["name"] == target_module:
                return module

    def process_modules(self) -> None:
        """Processes the desired modules by adding themselves and their dependencies to a graph and also adding their dependencies to the desired_module_names list.
        
        Returns: None
        """
        self.desired_modules = []
        self.dependency_graph = nx.DiGraph()
        for module_name in self.desired_module_names:
            spec = self.get_module_spec(module_name)
            self.desired_modules.append(spec)
            # If module has no dependencies then its first entry will be None
            if not spec["dependencies"][0] or spec["dependencies"][0] == "None":
                continue
            self.add_module_dependencies_to_graph(spec["dependencies"], spec["name"])
            self.add_module_dependencies_to_desired_modules(spec["dependencies"])

    def order_desired_modules(self) -> None:
        """Orders desired_modules, which is a list of module specs, by using deployment_order, which is an ordered list of module names, so that the configuration file given to the user flows properly. 

        Returns: None
        """
        deployment_order = self.get_deployment_order()

        def custom_key(item):
            return (
                deployment_order.index(item["name"])
                if item["name"] in deployment_order
                else len(deployment_order)
            )

        sorted_modules = sorted(self.desired_modules, key=custom_key)
        self.desired_modules = sorted_modules

    def load_desired_modules(self) -> None:
        """In order for a module to be considered loaded it needs to be in the desired_module_names list, required_variables declared, have its dependencies added to the graph, and be correctly placed in the deployment order.

        Returns: None
        """
        logging.info("Loading desired modules")
        # Loading our configuration file
        if os.path.exists("configuration.yaml"):
            self.configuration = self.load_yaml("configuration.yaml")
        # Build our initial list of desired_module_names
        _ = self.get_desired_modules()
        # Use that list to get all their dependencies, add them to a graph, and then get the module's spec
        self.process_modules()
        # Now we order the modules based on which dependencies come first
        self.order_desired_modules()

    def build_configuration_file(self, refresh_available_configuration: bool = False) -> None:
        """Builds the main configuration file which is used to configure the each module's playbook.

        Args: refresh_available_configuration (bool, optional): If True, the available configuration will be refreshed. Defaults to False.

        Returns: None
        """
        logging.info("Building configuration file")
        if refresh_available_configuration:
            configuration_file = self.load_yaml("configuration.yaml")
        else:
            configuration_file = {"Modules": []}
        self.load_desired_modules()
        for module in self.desired_modules:
            # We are making the configuration block more human friendly which means more code for us but that is fine
            configuration_block = {
                "Name": module["name"],
                "Required Variables": [
                    {
                        "Name": v["name"],
                        "Description": v["description"],
                        "Value": v["default"],
                    }
                    for v in module["required_variables"]
                ],
            }
            # TO-DO: Make this more robust. We are only adding it in if the name does not exist. But if the name exists we should verify the variables block
            config_block_exists = any(
                x["Name"] == configuration_block["Name"]
                for x in configuration_file["Modules"]
            )
            if config_block_exists:
                logging.info(f"Skipping '{module['name']}' since it already exists in the configuration file")
                continue
            configuration_file["Modules"].append(configuration_block)
        self.save_yaml("configuration.yaml", configuration_file)
        self.configuration = configuration_file        

    def validate_configuration_file(self) -> bool:
        """Validates that the user has provided correct configuration values for the selected modules.

        Returns: bool: True if the configuration is valid, False if not.
        """
        logging.info("Validating configuration file")
        valid = True
        self.load_desired_modules()
        # TO-DO: Support netaddr type for networking configuration variables
        type_mappings = {
            "str": str,
            "list": list,
            "int": int,
            "bool": bool,
            "dict": dict,
        }
        for i, module in enumerate(self.configuration["Modules"]):
            module_spec = self.get_module_spec(module["Name"])
            for j, variable in enumerate(module["Required Variables"]):
                valid_name = any(
                    x["name"] == variable["Name"]
                    for x in module_spec["required_variables"]
                )
                if not valid_name:
                    logging.warning(f"Invalid variable name '{variable['Name']}' for module '{module['Name']}'")
                    valid = False
                    invalid_name = self.configuration["Modules"][i]["Required Variables"][j]["Name"]
                    variable_names = ','.join([v['name'] for v in module_spec['variables']])
                    error_message = f" <--- unexpected variable name, expected one of these '{variable_names}'"
                    self.configuration["Modules"][i]["Required Variables"][j]["Name"] = (invalid_name + error_message)
                expected_type = type_mappings[module_spec["required_variables"][j]["type"]]
                valid_value = isinstance(variable["Value"], expected_type)
                if not valid_value:
                    logging.warning(f"Invalid variable type '{type(variable['Value'])}' for '{variable['Value']}' for module '{module['Name']}'")
                    valid = False
                    invalid_value = self.configuration["Modules"][i]["Required Variables"][j]["Value"]
                    error_message = f" <--- invalid type, expected a '{module_spec['type']}')"
                    self.configuration["Modules"][i]["Required Variables"][j]["Name"] = (invalid_value + error_message)
        if not valid:
            self.save_yaml("configuration.yaml", self.configuration)
        return valid

    def gather_configuration_variables(self) -> None:
        """Gathers all the configuration variables from the configuration file and puts them into our configuration_variables dictionary for use in the Ansible playbooks.
        
        Returns: None
        """
        logging.info("Gathering all variables from configuration file")
        for module in self.configuration["Modules"]:
            for variable in module["Required Variables"]:
                self.configuration_variables[variable["Name"]] = variable["Value"]
        logging.info("Generating random password for root user on hypervisor")
        root_password_hash = self.generate_and_hash_password() 
        self.configuration_variables["root_password_hash"] = root_password_hash
        logging.info("Generating Ansible SSH keypair")
        ansible_public_key = self.generate_ansible_ssh_keypair()
        self.configuration_variables["ansible_public_key"] = ansible_public_key

    def deploy_homelab(self, debug_playbook: str = "") -> None:
        """Main execution logic for the project.

        Args: debug_playbook (str, optional): The name of the module to execute the playbook for. Used for debugging.

        Returns: None
        """
        print("Deploying homelab in a box!")
        self.load_desired_modules()
        for module in self.desired_modules:
            self.link_module_roles(module["name"])
        deployment_order = self.get_deployment_order()
        self.gather_configuration_variables()
        self.execute_ansible_playbooks(deployment_order)


def main(hiab: HomeLabInABox = None) -> int:
    """This is what is defaulted to when the project is run. It will guide the user through the process of deploying the homelab.

    Args: hiab (HomeLabInABox): An instance of the HomeLabInABox class.

    Returns: int: The return code for the script.
    """
    if not hiab:
        hiab = HomeLabInABox()

    # Module Choices Logic
    if os.path.exists("module_choices.yaml"):
        logging.info("Existing module choices found")
        choice = input("Existing module choices found. Would you like to modify it? (y/n): ")
        if choice.lower() == "y":
            hiab.gather_modules(refresh_available_modules=True)
            subprocess.call(["vim", "module_choices.yaml"])
    else:
        logging.info("No module choices have been made yet")
        hiab.gather_modules()
        input("When ready hit enter and then pick out the modules you would like to have: ")
        subprocess.call(["vim", "module_choices.yaml"])

    # Module Validation Loop
    while not hiab.validate_modules():
        subprocess.call(["vim", "module_choices.yaml"])
    logging.info("Module choices are valid")

    # Configuration Logic
    if os.path.exists("configuration.yaml"):
        logging.info("Existing configuration file found")
        choice = input("Existing configuration file found. Would you like to modify it? (y/n): ")
        if choice.lower() == "y":
            hiab.build_configuration_file(refresh_available_configuration=True)
            subprocess.call(["vim", "configuration.yaml"])
    else:
        logging.info("Configuration file does not exist")
        hiab.build_configuration_file()
        input("When ready hit enter and then fill out all the variables for each module in this configuration file: ")
        subprocess.call(["vim", "configuration.yaml"])

    # Configuration Validation Loop
    while not hiab.validate_configuration_file():
        subprocess.call(["vim", "configuration.yaml"])
    logging.info("Configuration file is valid")

    print("Preflight checks complete!")
    hiab.deploy_homelab()
    return 0


def execute_arguments(args: argparse.Namespace) -> None:
    """Executes the arguments provided to the script. Arguments are mutually exclusive so only one will be executed. Mainly used for debugging and development.

    Args: args (argparse.Namespace): The arguments provided to the script.

    Returns: None
    """
    # TO-DO: Print statements for the validate calls so that the user knows they are good 
    hiab = HomeLabInABox(debug=args.debug)
    if len(sys.argv) == 2 and args.debug:
        rc = main(hiab)
        sys.exit(rc)
    if args.gather_modules:
        hiab.gather_modules()
    elif args.validate_modules:
        if not hiab.validate_modules():
            logging.warn(
                "Invalid module section detected. Please correct issues in module_choices.yaml"
            )
            sys.exit(-1)
    elif args.build_configuration:
        hiab.build_configuration_file()
    elif args.validate_configuration:
        if not hiab.validate_configuration_file():
            logging.warn(
                "Invalid configuration file detected. Please correct issues in configuration.yaml"
            )
            sys.exit(-1)
    elif args.skip_preflight:
        logging.info("Skipping preflight checks.")
        hiab.deploy_homelab()
        sys.exit(0)
    elif args.execute_module:
        module_name = args.execute_module
        logging.info(f"Executing specifically the '{module_name}' module")
        hiab.deploy_homelab(debug_playbook=module_name)
        sys.exit(0)
    else:
        logging.info("No valid arguments provided. Use --help for options.")
    sys.exit(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="A script to manage a modular homelab deployment."
    )
    parser.add_argument(
        "--gather-modules",
        action="store_true",
        help="Gathers a list of available modules.",
    )
    parser.add_argument(
        "--validate-modules",
        action="store_true",
        help="Validates that the user has requested valid and existing modules.",
    )
    parser.add_argument(
        "--build-configuration",
        action="store_true",
        help="Builds the needed configuration file based on user module selection.",
    )
    parser.add_argument(
        "--validate-configuration",
        action="store_true",
        help="Validates that the user has provided correct configuration settings for the selected modules.",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skips the preflight checks and deploys the homelab in a box immediately.",
    )
    parser.add_argument(
        "--execute-module",
        type=str,
        help="Skips execution of all other modules except this one. Useful for debugging a specific playbook.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enables debug logging which will increase the amount of logging and print said logging to stdout.",
    )
    args = parser.parse_args()
    if len(sys.argv) > 1:
        execute_arguments(args)
    else:
        return_code = main()
        if return_code == 0:
            print("Homelab in a box deployment complete!")
        logging.info(f"Execution ending with {return_code}")
        sys.exit(return_code)
