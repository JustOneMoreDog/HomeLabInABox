from ansible_runner import Runner
import ansible_runner
import io 
import json
import contextlib
import yaml
import re

class AnsibleRunTimeExecution(Exception):
    pass


class DataParsingException(Exception):
    pass 


class AnsibleWrapper:
    def __init__(self, inventory: dict, playbook: dict, roles_directory: str, module_name: str):
        self.inventory = inventory
        self.playbook = playbook
        self.roles_directory = roles_directory
        self.event_data = dict()
        self.module_name = module_name

    def run(self) -> None:
        redirected_stdout = io.StringIO()
        runner = Runner
        with contextlib.redirect_stdout(redirected_stdout):
            runner = ansible_runner.run(
                playbook=self.playbook,
                inventory=self.inventory,
                artifact_dir='logs/ansible-runner',
                json_mode=True,
                roles_path=self.roles_directory,
                # Warnings will not be in JSON format and thus break everything
                envvars = {
                    "ANSIBLE_LOCALHOST_WARNING": False,
                    "ANSIBLE_INVENTORY_UNPARSED_WARNING": False,    
                }
            )
        json_data = self.construct_json_data(redirected_stdout, runner.rc)
        self.parse_json_data(json_data)

    def construct_json_data(self, stdout: io.StringIO, rc: int) -> dict:
        # Breaks all the output down into individual lines
        split_stdout = stdout.getvalue().strip().splitlines()
        # Removes all the warnings and raises a critical error for invalid JSON that we did not expect
        cleaned_stdout = self.clean_stdout(split_stdout)
        # If the playbook had a failure then we find it and raise it
        if rc != 0:
            error_message = self.locate_execution_errors(cleaned_stdout)
            raise AnsibleRunTimeExecution(error_message)
        # Only now can we finally construct the output into a single piece of data (that we currently do not use)
        constructed_json_stdout = ",".join(cleaned_stdout)
        constructed_json_string = ''.join(["[", constructed_json_stdout, "]"])
        try:
            json_data = json.loads(constructed_json_string)
            return json_data
        except:
            raise DataParsingException("I have absolutely no idea how we got here and that is a problem.")

    def locate_execution_errors(self, stdout: list) -> str:
        for entry, line in enumerate(stdout):
            json_data = json.loads(line)
            if not "stdout" in json_data:
                continue
            if not json_data["stdout"]:
                continue
            is_an_error = re.search(r'.*: FAILED! .*', json_data["stdout"])
            if not is_an_error:
                continue
            task_name, task_action, task_host, task_host_ip = self.get_task_data(json_data)
            error_message = f"The '{self.module_name}' module ran the '{task_name}' task which used '{task_action}' against the '{task_host}' host located at '{task_host_ip}' and it caused the following error:\n{json_data['stdout']}"
            return error_message

    def clean_stdout(self, stdout: list) -> list:
        cleaned_stdout = []
        print("Checking for warnings")
        for entry, line in enumerate(stdout):
            is_a_warning = re.search(r'.*\[WARNING\]: (.*) .*\[0m', line)
            if is_a_warning:
                self.parse_warning_message(is_a_warning, entry, stdout)
                continue
            if not self.is_valid_json(line):
                raise DataParsingException(f"The following line of output was not able to be cleaned:\n{line}")
            cleaned_stdout.append(line)  
        return cleaned_stdout

    def is_valid_json(self, data: str) -> bool:
        try:
            _ = json.loads(data)
            return True
        except:
            return False

    def parse_warning_message(self, match: re.Match, entry: int, lines: list) -> None:
        # TO-DO: What happens if the warning is the first line in the output
        message = match.group(1)
        has_previous_entry = (entry - 1) < 0
        if not has_previous_entry:
            print(f"We got the following warning in the '{self.module_name}' module but were unable to determine what caused it:\n'{message}'")
            return    
        previous_entry = json.loads(lines[entry - 1])
        task_name, task_action, task_host, task_host_ip = self.get_task_data(previous_entry)
        print(f"The '{self.module_name}' module ran the '{task_name}' which used '{task_action}' against the '{task_host}' host located at '{task_host_ip}' and it caused the following warning:\n'{message}'")

    def get_task_data(self, task: dict) -> tuple[str, str, str, str]:
        task_name, task_action, task_host, task_host_ip = 'Unknown', 'Unknown', 'Unknown', 'Unknown'
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

    def parse_json_data(self, runner_output: list) -> None:
        for data in runner_output:
            if "event_data" not in data:
                continue
            if "res" not in data["event_data"]:
                continue
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
                "changed": changed
            }
            if hostname not in self.event_data:
                self.event_data[hostname] = []
            self.event_data[hostname].append(event)

    def clean_result_data(self, result: dict) -> tuple[dict, bool]:
        result_data = result
        changed = result_data["changed"]
        unwanted_keys = [
            'warnings',
            'deprecations',
            '_ansible_verbose_override',
            '_ansible_no_log',
            '_ansible_verbose_always',
            'changed'
        ]
        for key in unwanted_keys:
            if key in result_data:
                del result_data[key]
        return result_data, changed


# if __name__ == '__main__':
#     with open('tester.yaml', 'r') as f:
#         playbook = yaml.safe_load(f)
#     with open('inventory/tester.yaml', 'r') as f:
#         inventory = yaml.safe_load(f)
#     ansible_job = AnsibleWrapper(playbook=playbook, inventory=inventory)
#     ansible_job.run()
