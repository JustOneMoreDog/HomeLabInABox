from ansible_runner import Runner
import ansible_runner
import re
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
        with self.console.status(f"Running {self.module_name} playbook...") as status:
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
            logging.warning(f"The '{self.module_name}' module failed to run successfully")
            error_message = self.locate_execution_errors()
            if not error_message:
                error_message = "We were unable to locate an error message for the failed playbook run"
            raise AnsibleRunTimeExecution(error_message)
            
    def locate_execution_errors(self) -> str:
        for host, events in self.all_event_data.items():
            error_message = self.check_host_for_execution_errors(host, events)
            if error_message:
                return error_message
        return ""
        
    def check_host_for_execution_errors(self, host: str, events: list[dict]) -> str:
        """Checks all the event data for a given host for any errors that occurred during the playbook run
        
        Args: 
            events (list[dict]): The event data from the host in question
            host (str): The host that the event data is from
        
        Returns: str: The error message if one is found, otherwise an empty string
        """
        logging.info(f"Checking for errors in the event data for '{host}'")
        for event in events:
            if "stdout" not in event:
                continue
            if not event["stdout"]:
                continue
            is_an_error = re.search(r".*: FAILED! .*", event["stdout"])
            if not is_an_error:
                continue
            task_name, task_action, task_host, task_host_ip = self.get_task_data(event)
            error_message = f"The '{self.module_name}' module ran the '{task_name}' task which used '{task_action}' against the '{task_host}' host located at '{task_host_ip}' and it caused the following error:\n{event['stdout']}"
            return error_message
        return ""
    
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
        if event['event'] not in self.valid_event_types:
            return
        task_name, task_action, task_host, task_host_ip = self.get_task_data(event)
        if task_name == "Unknown":
            print("WACKY")
            print(event)
        self.parse_event_data(event)
        self.console.log(f"[{task_name}][{task_action}][{task_host}][{task_host_ip}]: Complete")

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
        print(f"Here is the data:\n{result}")
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
