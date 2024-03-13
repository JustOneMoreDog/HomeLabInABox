import networkx as nx
import yaml
import os
import logging
import argparse
import sys
import subprocess
from AnsibleWrapper import AnsibleWrapper

class ModuleConfigurationError(Exception):
    """Raised when a module configuration is invalid."""
    pass


class HomeLabInABox:
    def __init__(self, debug: bool=False):
        self.setup_logging(debug)
        self.desired_module_names = []
        self.desired_modules = []
        self.dependency_graph = nx.DiGraph()
        self.configuration = {}
        self.terraform_inventory = os.path.abspath(os.path.join(os.getcwd(), "inventory", "terraform_inventory.yaml"))
        self.roles_directory = os.path.abspath(os.path.join(os.getcwd(), "roles"))
        self.configuration_variables = {}
        self.all_modules = self.get_all_modules()

    def setup_logging(self, debug_mode) -> None:
        # Replace the placeholder below with your logging configuration
        logging.basicConfig(level=logging.INFO)

    def load_yaml(self, yaml_path: str) -> dict:  
        try:
            with open(yaml_path, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"YAML file '{yaml_path}' not found.")
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"Invalid YAML in file '{yaml_path}': \n{e}")
    
    def save_yaml(self, filepath: str, data: dict) -> None:
        if not isinstance(data, dict):
            raise TypeError("The 'data' argument must be a dictionary.")
        try:
            if not os.path.isabs(filepath):
                filepath = os.path.abspath(os.path.join(os.getcwd(), filepath))
            # Create any necessary directories in the path
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, 'w') as outfile:
                yaml.safe_dump(data, outfile, default_flow_style=False, sort_keys=False)
        except OSError as e:
            raise OSError(f"Error saving YAML file '{filepath}': {e}")
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"Error serializing data to YAML: {e}") 
        os.path

    def get_configuration(self) -> tuple[dict, list]:
        config_path = 'configuration.yaml'
        config_data = self.load_yaml(config_path)  
        return config_data['variables'], config_data['modules']

    def get_module_requirements(self, module_path: str) -> tuple[list, list]:
        requirements_file = os.path.join(module_path, 'requirements.yaml')
        requirements = self.load_yaml(requirements_file)
        if 'dependencies' not in requirements or 'variables' not in requirements:
            raise ModuleConfigurationError(f"'{requirements_file}' is missing dependencies or variables section.")
        return requirements.get('dependencies', []), requirements.get('variables', [])
    
    def add_module_dependencies_to_graph(self, module_dependencies: list, module_name: str) -> None:
        self.dependency_graph.add_node(module_name)
        # If module has no dependencies then its first entry will be None
        if not module_dependencies[0] or module_dependencies[0] == "None":
            return
        for dependency in module_dependencies:
            logging.info(f"Adding '{dependency}' as dependency for '{module_name}'")
            if dependency not in self.desired_module_names:
                logging.info(f"Adding '{dependency}' to desired module names list")
                self.desired_module_names.append(dependency)
            self.dependency_graph.add_edge(dependency, module_name)
            self.check_for_cycles()

    def check_for_cycles(self) -> None:
        if nx.is_directed_acyclic_graph(self.dependency_graph):
            return
        logging.warning("Cycle detected")
        cycles_list = list(nx.simple_cycles(self.dependency_graph))
        cycles = '\n'.join([f"  - {' -> '.join(cycle)}" for cycle in cycles_list])
        ModuleConfigurationError(f"The following cycles have been detected and operation cannot continue\n{cycles}")
    
    def verify_module_variables(self, variables: list, module_name: str) -> None:
        for variable in variables:
            if variable not in self.configuration_variables:
                # TO-DO: Ask the user for what that variable should be and then we update the config accordingly
                raise ModuleConfigurationError(f"'{variable}' is required by the '{module_name}' module and is missing in our configuration.")
    
    def validate_module(self, module_name: str) -> None:
        module_path = os.path.join('Modules', module_name)
        if not os.path.exists(module_path):
            raise ModuleConfigurationError(f"Module '{module_name}' not found in the 'Modules' directory.")
        required_files = ['roles', 'requirements.yaml', 'playbook.yaml', 'README.md'] 
        for item in required_files:
            item_path = os.path.join(module_path, item)
            if not os.path.exists(item_path):
                raise ModuleConfigurationError(f"Required item '{item}' not found in module '{module_name}'.")
            
    def link_module_roles(self, module_name: str) -> None:
        roles_dir = os.path.join('Modules', module_name, 'roles')
        target_roles_dir = 'roles'
        for role_name in os.listdir(roles_dir):
            if not os.path.isdir(role_name):
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
    
    def get_deployment_order(self) -> list:
        return list(nx.topological_sort(self.dependency_graph))

    def load_module(self, module: dict) -> None:
        logging.info(f"Loading module '{module['name']}'")
        module_path = os.path.join(os.path.curdir, "Modules", module['name'])
        module_dependencies, module_variables = self.get_module_requirements(module_path)
        self.verify_module_variables(module_variables, module['name'])
        self.add_module_dependencies_to_graph(module_dependencies, module['name'])

    def get_module_playbook(self, module: str) -> dict:
        playbook_path = os.path.join("Modules", module, "playbook.yaml")
        playbook_data = self.load_yaml(playbook_path)
        # TO-DO: Handle the situation where the playbook already has vars defined (it really shouldn't though)
        for play in playbook_data:
            play["vars"] = self.configuration_variables
        return playbook_data

    def get_module_inventory(self, playbook: list[dict]) -> str:
        just_local_host = True
        for play in playbook:
            if play['hosts'] != 'localhost':
                just_local_host = False
        if not just_local_host:
            return self.terraform_inventory
        return '/dev/null'
             
    def execute_ansible_playbooks(self, modules: list[str]) -> None:
        for module in modules:
            playbook = self.get_module_playbook(module)
            inventory = self.get_module_inventory(playbook)
            ansible_runner = AnsibleWrapper(inventory=inventory, playbook=playbook, roles_directory=self.roles_directory, module_name=module)
            logging.info(f"Executing the '{module}' playbook")
            ansible_runner.run()

    def get_all_modules(self) -> list[dict]:
        specs = []
        for module in os.listdir("Modules"):
            module_path = os.path.join("Modules", module)
            if not os.path.isdir(module_path) or module == ".template":
                continue
            spec_path = os.path.join(module_path, "spec.yaml")
            spec = self.load_yaml(spec_path)
            spec['name'] = module
            specs.append(spec)
        return specs
    
    def gather_modules(self, refresh_available_modules: bool = False) -> None:
        logging.info("Gathering modules")
        if refresh_available_modules:
            modules = self.load_yaml('module_choices.yaml')
            modules['available_modules'] = []
        else:
            modules = {
                'wanted_modules': ['Put your desired modules here', '', ''],
                'available_modules': []
            }
        for module in self.all_modules:
            modules['available_modules'].append(
                {
                    'Name': module['name'],
                    'Description': module['description']
                }
            )
        self.save_yaml('module_choices.yaml', modules)

    def validate_modules(self) -> bool:
        logging.info("Validating modules")
        valid = True
        module_choices = self.get_desired_modules()
        for module in self.desired_module_names:
            # TO-DO: More robust checking for accidental user input
            if not module:
                continue
            a_valid_module_choice = any(x["name"] == module for x in self.all_modules)
            if not a_valid_module_choice:
                valid = False
                module_choices['wanted_modules'][module] = module + " <--- invalid"
        if not valid:
            self.save_yaml('module_choices.yaml', module_choices)        
        return valid
    
    def get_desired_modules(self) -> dict:
        self.desired_module_names = []
        module_choices = self.load_yaml('module_choices.yaml')
        for chosen_module in module_choices['wanted_modules']:
            # TO-DO: More robust checking for accidental user input
            if not chosen_module:
                continue
            module = chosen_module.strip()
            self.desired_module_names.append(module)
        return module_choices
    
    def get_module_spec(self, target_module: str) -> dict:
        for module in self.all_modules:
            if module["name"] == target_module:
                return module

    def process_modules(self) -> None:
        self.desired_modules = []
        self.dependency_graph = nx.DiGraph()
        for module_name in self.desired_module_names:
            spec = self.get_module_spec(module_name)
            self.desired_modules.append(spec)
            self.add_module_dependencies_to_graph(spec["dependencies"], spec["name"])

    def order_desired_modules(self) -> None:
        deployment_order = self.get_deployment_order()
        def custom_key(item):
            return deployment_order.index(item["name"]) if item["name"] in deployment_order else len(deployment_order)
        sorted_modules = sorted(self.desired_modules, key=custom_key)
        self.desired_modules = sorted_modules

    def load_desired_modules(self) -> None:
        # Build our initial list of desired_module_names
        _ = self.get_desired_modules()
        # Use that list to get all their dependencies, add them to a graph, and then get the module's spec
        self.process_modules()
        # Now we order the modules based on which dependencies come first
        self.order_desired_modules()
    
    def build_configuration_file(self, refresh_available_configuration: bool = False) -> None:
        if refresh_available_configuration:
            configuration_file = self.load_yaml('configuration.yaml')
        else:
            configuration_file = {
                "Modules": []
            }
        self.load_desired_modules()
        for module in self.desired_modules:
            configuration_block = {
                "Name": module["name"],
                "Required Variables": [{
                    "Name": v["name"],
                    "Description": v["description"],
                    "Value": v["default"]
                } for v in module["required_variables"]]
            }
            # TO-DO: Make this more robust. We are only adding it in if the name does not exist. But if the name exists we should verify the variables block
            config_block_exists = any(x["Name"] == configuration_block["Name"] for x in configuration_file["Modules"])
            if config_block_exists:
                logging.info(f"Skipping '{module['name']}' since it already exists in the configuration file")
            else:
                configuration_file["Modules"].append(configuration_block)
        self.save_yaml("configuration.yaml", configuration_file)

    def get_configuration(self) -> None:
        self.configuration = self.load_yaml("configuration.yaml")
    
    def validate_configuration_file(self) -> bool:
        valid = True
        self.load_desired_modules()
        self.get_configuration()
        type_mappings = {
            "str": str,
            "list": list,
            "int": int,
            "bool": bool,
            "dict": dict
        }
        for i, module in enumerate(self.configuration["Modules"]):
            module_spec = self.get_module_spec(module["Name"])
            for j, variable in enumerate(module["Required Variables"]):
                valid_name = any(x["name"] == variable["Name"] for x in module_spec["required_variables"])
                if not valid_name:
                    valid = False
                    invalid_name = self.configuration["Modules"][i]["Required Variables"][j]["Name"]
                    self.configuration["Modules"][i]["Required Variables"][j]["Name"] = invalid_name + f" <--- unexpected name, expected one of these '{','.join([v['name'] for v in module_spec['variables']])}'"
                expected_type = type_mappings[module_spec['required_variables'][j]["type"]]
                valid_value = isinstance(variable["Value"], expected_type)
                if not valid_value:
                    valid = False
                    invalid_value = self.configuration["Modules"][i]["Required Variables"][j]["Value"] 
                    self.configuration["Modules"][i]["Required Variables"][j]["Name"] = invalid_value + f" <--- invalid type, expected a '{module_spec['type']}')"
        if not valid:
            self.save_yaml('configuration.yaml', self.configuration)
        return valid
    
    def gather_configuration_variables(self) -> None:
        for module in self.configuration['Modules']:
            for variable in module["Required Variables"]:
                self.configuration_variables[variable["Name"]] = variable["Value"] 

    def deploy_homelab(self, debug_playbook: str='') -> None:
        # Now that we have validated all the modules we can symlink their roles into our main roles folder
        logging.info("Linking roles")
        for module in self.desired_modules:
            self.link_module_roles(module['name'])
        logging.info("Getting deployment order")
        deployment_order = self.get_deployment_order()
        print(f"Deployment order is: '{' -> '.join(deployment_order)}'")
        logging.info(f"Gathering all variables")
        self.gather_configuration_variables()
        self.execute_ansible_playbooks(deployment_order)
        return


def main(hiab: HomeLabInABox) -> int:
    if not hiab:
        hiab = HomeLabInABox()
    # Module Choices Logic
    if os.path.exists("module_choices.yaml"):
        print("Existing module choices file found")
        choice = input("Would you like to modify it? (y/n): ")
        if choice.lower() == 'y':
            hiab.gather_modules(refresh_available_modules=True)
            subprocess.call(['vim', 'module_choices.yaml'])    
    else:
        print("No module choices have been made yet building list of available modules")
        hiab.gather_modules()
        input("When ready hit enter and then pick out the modules you would like to have: ")
        subprocess.call(['vim', 'module_choices.yaml']) 

    # Module Validation Loop
    while True:
        if hiab.validate_modules():
            print("Module choices are valid")
            break
        logging.warn("Invalid module section detected throwing it back to user")
        subprocess.call(['vim', 'module_choices.yaml'])

    # Configuration Logic
    if os.path.exists("configuration.yaml"):
        print("Existing configuration file found")
        choice = input("Would you like to modify it? (y/n): ")
        if choice.lower() == 'y':
            hiab.build_configuration_file(refresh_available_configuration=True)
            subprocess.call(['vim', 'configuration.yaml'])    
    else:
        print("Configuration file does not exist building one now")
        hiab.build_configuration_file()
        input("When ready hit enter and then fill out the configuration file with the values of your choice: ")
        subprocess.call(['vim', 'configuration.yaml']) 

    # Configuration Validation Loop
    while True:
        if hiab.validate_configuration_file():
            print("Configuration file is valid")
            break
        logging.warn("Invalid configuration file detected throwing it back to user")
        subprocess.call(['vim', 'configuration.yaml'])
    
    print("Preflight checks complete. Deploying the HomeLabInABox!")
    hiab.deploy_homelab()
    return 0

def execute_arguments(args: argparse.Namespace) -> None:
    hiab = HomeLabInABox(debug=args.debug)
    if len(sys.argv) == 2 and args.debug:
        rc = main(hiab)
        sys.exit(rc)
    if args.gather_modules:
        hiab.gather_modules()
    elif args.validate_modules:
        logging.info("Validating modules...")
        if not hiab.validate_modules():
            logging.warn("Invalid module section detected. Please correct issues in module_choices.yaml")
            sys.exit(-1)
    elif args.build_configuration:
        hiab.build_configuration_file()
    elif args.validate_configuration:
        logging.info("Validating configuration...")
        if not hiab.validate_configuration_file():
            logging.warn("Invalid configuration file detected. Please correct issues in configuration.yaml")
            sys.exit(-1)
    elif args.deploy_homelab:
        logging.info("Deploying homelab...")
        rc = main(hiab)
        sys.exit(rc)
    elif args.execute_module:
        module_name = args.execute_module
        logging.info(f"Executing specifically the '{module_name}' module")
        hiab.deploy_homelab(debug_playbook=module_name)
    else:
        logging.info("No valid arguments provided. Use --help for options.")
    sys.exit(0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='A script to manage a modular homelab deployment.')
    parser.add_argument('--gather-modules', 
                    action='store_true',
                    help='Gathers a list of available modules.')
    parser.add_argument('--validate-modules',
                    action='store_true', 
                    help='Validates that the user has requested valid and existing modules.')
    parser.add_argument('--build-configuration',
                    action='store_true', 
                    help='Builds the needed configuration file based on user module selection.')
    parser.add_argument('--validate-configuration',
                    action='store_true',
                    help='Validates that the user has provided correct configuration settings for the selected modules.')
    parser.add_argument('--deploy-homelab', 
                    action='store_true',
                    help='Executes the selected modules with their configuration to deploy the homelab.')
    parser.add_argument('--execute-module', 
                    type=str,
                    help='Skips execution of all other modules except this one. Useful for debugging a specific playbook.')
    parser.add_argument('--debug', 
                    action='store_true',
                    help='Enables debug logging which will increase the amount of logging and print said logging to stdout.')
    args = parser.parse_args()
    if len(sys.argv) > 1:
        execute_arguments(args)
    else:
        return_code = main(None, args)
    logging.info(f"Execution ending with {return_code}")
    sys.exit(return_code)
