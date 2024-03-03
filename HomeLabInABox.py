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
            logging.error(f"You must have at least 1 module selected")
            raise SystemExit(1)
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
            logging.error(f"YAML file '{yaml_path}' not found.")
            raise SystemExit(1)  
        except yaml.YAMLError as e:
            logging.error(f"Invalid YAML in file '{yaml_path}': {e}")
            raise SystemExit(1)      

    def get_configuration(self) -> tuple[dict, list]:
        config_path = 'configuration.yaml'
        config_data = self.load_yaml(config_path)  
        return config_data.get('variables', {}), config_data.get('modules', [])

    def get_module_requirements(self, module_path: str) -> tuple[list, list]:
        requirements_file = os.path.join(module_path, 'requirements.yaml')
        requirements = self.load_yaml(requirements_file)
        if 'dependencies' not in requirements or 'variables' not in requirements:
            logging.error(f"'{requirements_file}' is missing dependencies or variables section.")
            raise SystemExit(1)
        return requirements.get('dependencies', []), requirements.get('variables', [])
    
    def add_module_dependencies_to_graph(self, module_dependencies: list, module_name: str) -> None:
        self.dependency_graph.add_node(module_name)
        for dependency in module_dependencies:
            if dependency not in self.desired_modules:
                self.desired_modules.append(dependency)
            self.dependency_graph.add_edge(module_name, dependency)

    def add_module(self, module: str, parent: str) -> None:
        if module not in self.desired_modules:
            self.verify_module(module)
            self.desired_modules.append(module)
            return
    
    def verify_module_variables(self, variables: list, module_name: str) -> None:
        for variable in variables:
            if variable not in self.configuration_variables:
                logging.error(f"'{variable}' is required by the '{module_name}' module and is missing in our configuration.")
                raise SystemExit(1)    

    def load_module(self, module: dict) -> None:
        module_path = os.path.join(os.path.curdir, "Modules", module['name'])
        module_dependencies, module_variables = self.get_module_requirements(module_path)
        self.verify_module_variables(module_variables, module['name'])
        self.add_module_dependencies_to_graph(module_dependencies, module['name'])


    def deploy_homelab(self) -> None:
        for module in self.desired_modules:
            # TODO: Make validate_module function
            self.validate_module(module)
            self.load_module(module)
        return

if __name__ == '__main__':
    hiab = HomeLabInABox()
    hiab.deploy_homelab()
