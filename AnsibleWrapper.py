from ansible_runner import Runner
import ansible_runner
import logging
from rich.console import Console


class AnsibleRunTimeExecution(Exception):
    pass


class DataParsingException(Exception):
    pass


class AnsibleWrapper:
    def __init__(self, inventory: dict, playbook: dict, roles_directory: str, module_name: str):
        logging.getLogger('HomeLabInABox')
        logging.info("Creating an AnsibleWrapper object")
        self.valid_event_types = ["runner_on_ok", "runner_on_failed", "runner_on_unreachable", "playbook_on_stats"]
        self.inventory = inventory
        self.playbook = playbook
        self.roles_directory = roles_directory
        self.all_event_data = {}
        self.module_name = module_name
        self.console = Console()

    def run(self) -> None:
        """Runs the Ansible playbook for a given module"""
        runner = Runner
        with self.console.status(f"Running {self.module_name} playbook...") as _:
            runner = ansible_runner.run(
                playbook=self.playbook,
                inventory=self.inventory,
                artifact_dir="logs/ansible-runner",
                json_mode=True,
                roles_path=self.roles_directory,
                event_handler=self.runner_event_callback,
                quiet=True
            )
        if runner.rc != 0:
            raise AnsibleRunTimeExecution("Ansible runner returned a non-zero exit code which means that an exception occurred during the playbook run that we did not catch with our event handler. This is very bad.")
        
    def runner_event_callback(self, event: dict) -> None:
        """Processes events from Ansible Runner
        
        Args: event (dict): The event data from the Ansible Runner

        Returns: None
        """
        if event['event'] == 'playbook_on_task_start':
            self.console.log(f"Running '{event['event_data']['name']}' against '{event['event_data']['play_pattern']}'")
            return
        if event['event'] == 'playbook_on_stats':
            # TO-DO: Implement a play recap parsing method to display the results of the playbook run
            self.console.log("Playbook run complete")
            return
        if event['event'] == 'runner_on_failed':
            error_message = self.get_execution_error_message(event)
            raise AnsibleRunTimeExecution(error_message)      
        if event['event'] not in self.valid_event_types:
            # TO-DO: Implement more functions to handle more event types
            return
        task_name, task_action, task_host, task_host_ip = self.get_task_data(event)
        self.parse_event_data(event)
        self.console.log(f"[{task_name}][{task_action}][{task_host}][{task_host_ip}]: Complete")
        
    def get_execution_error_message(self, event: dict) -> str:
        """When we get a runner_on_failed event we use this function to extract relevant information from the event data.
        
        Args: 
            events dict: The event data that contains an error message
        
        Returns: str: A structured error message that we can now raise to the user
        """
        logging.info("Checking for errors in the event data")
        task_name, task_action, task_host, task_host_ip = self.get_task_data(event)
        error_message = f"The '{self.module_name}' module ran the '{task_name}' task which used '{task_action}' against the '{task_host}' host located at '{task_host_ip}' and it caused the following error:\n{event['stdout']}"
        return error_message

    def get_task_data(self, task: dict) -> tuple[str, str, str, str]:
        """Extracts the task data from the event data
        
        Args: task (dict): The event data from the Ansible Runner

        Returns: tuple[str, str, str, str]: The task name, task action, host, and host IP
        """
        task_name, task_action, task_host, task_host_ip = (
            "Unknown",
            "Unknown",
            "Unknown",
            "Unknown",
        )
        have_event_data = "event_data" in task
        have_task_name = have_event_data and "task" in task["event_data"]
        have_task_action = have_event_data and "task_action" in task["event_data"]
        have_task_host = have_event_data and "host" in task["event_data"]
        have_task_host_ip = have_event_data and "remote_addr" in task["event_data"]
        if not have_event_data:
            return task_name, task_action, task_host, task_host_ip
        if have_task_name:
            task_name = task["event_data"]["task"]
        if have_task_action:
            task_action = task["event_data"]["task_action"]
        if have_task_host:
            task_host = task["event_data"]["host"]
        if have_task_host_ip:
            task_host_ip = task["event_data"]["remote_addr"]
        return task_name, task_action, task_host, task_host_ip

    def parse_event_data(self, data: dict) -> None:
        """Parses the event data from the Ansible Runner. Currently we do not use the information we are storing here but it is available for future use.
        
        Args: data (dict): The event data from the Ansible Runner
        
        Returns: None
        """
        if "event_data" not in data:
            return
        if "res" not in data["event_data"]:
            return
        result_data, changed = self.clean_result_data(data["event_data"]["res"])
        playbook = data["event_data"]["playbook"]
        hostname = data["event_data"]["host"]
        ip_address = data["event_data"]["remote_addr"]
        task_name = data["event_data"]["task"]
        task_action = data["event_data"]["task_action"]
        event = {
            "playbook": playbook,
            "hostname": hostname,
            "ip_address": ip_address,
            "task_name": task_name,
            "task_action": task_action,
            "result_data": result_data,
            "changed": changed,
        }
        if hostname not in self.all_event_data:
            self.all_event_data[hostname] = []
        self.all_event_data[hostname].append(event)
    
    def clean_result_data(self, result: dict) -> tuple[dict, bool]:
        """Cleans the result data by pulling out all the unwanted data
        
        Args: result (dict): The result data from the Ansible Runner

        Returns: tuple[dict, bool]: The cleaned result data and a boolean indicating if the result data has changed
        """
        result_data = result
        changed = result_data["changed"]
        unwanted_keys = [
            "warnings",
            "deprecations",
            "_ansible_verbose_override",
            "_ansible_no_log",
            "_ansible_verbose_always",
            "changed",
        ]
        for key in unwanted_keys:
            if key in result_data:
                del result_data[key]
        return result_data, changed
