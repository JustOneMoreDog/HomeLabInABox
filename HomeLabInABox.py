import networkx as nx
import yaml
import os
import logging

class ModuleConfigurationError(Exception):
    """Raised when a module configuration is invalid."""
    pass


class HomeLabInABox:
    def __init__(self):
        self.setup_logging()
        self.configuration_variables, self.desired_modules = self.get_configuration()
        if not self.desired_modules:
            raise ModuleConfigurationError(f"You must have at least 1 module selected")
        self.dependency_graph = nx.DiGraph()
        self.loaded_modules = []  

    def setup_logging(self) -> None:
        # Replace the placeholder below with your logging configuration
        logging.basicConfig(level=logging.INFO)  # Basic for now

    def load_yaml(self, yaml_path: str) -> dict:  
        try:
            with open(yaml_path, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            raise ModuleConfigurationError(f"YAML file '{yaml_path}' not found.")
        except yaml.YAMLError as e:
            raise ModuleConfigurationError(f"Invalid YAML in file '{yaml_path}': \n{e}")

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
            if dependency not in self.desired_modules:
                logging.info(f"Adding '{dependency}' to modules list")
                self.desired_modules.append({'name': dependency, 'vars': {}})
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
        required_files = ['roles', 'requirements.yaml', 'playbook.yaml.j2', 'README.md'] 
        for item in required_files:
            item_path = os.path.join(module_path, item)
            if not os.path.exists(item_path):
                raise ModuleConfigurationError(f"Required item '{item}' not found in module '{module_name}'.")
            
    def link_module_roles(self, module_name: str) -> None:
        roles_dir = os.path.join('Modules', module_name, 'roles')
        target_roles_dir = 'roles'
        for role_name in os.listdir(roles_dir):
            source_role = os.path.abspath(os.path.join(roles_dir, role_name))
            target_role = os.path.abspath(os.path.join(target_roles_dir, role_name))
            if os.path.exists(target_role):
                # TO-DO: How do we handle modules that have duplicate named roles?
                logging.warning(f"Role '{role_name}' already exists in the main 'roles' directory.")
                continue
            print(f"Source: '{source_role}', Destination: '{target_role}'")
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

    def deploy_homelab(self) -> None:
        logging.info("Loading modules")
        for module in self.desired_modules:
            self.validate_module(module['name'])
            self.load_module(module)
        self.check_for_cycles()
        # Now that we have validated all the modules we can symlink their roles into our main roles folder
        logging.info("Linking roles")
        for module in self.desired_modules:
            self.link_module_roles(module['name'])
        logging.info("Getting deployment order")
        deployment_order = self.get_deployment_order()
        print(f"Deployment order is: '{' -> '.join(deployment_order)}'")
        return

if __name__ == '__main__':
    hiab = HomeLabInABox()
    hiab.deploy_homelab()
