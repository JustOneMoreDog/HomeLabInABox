from ansible_runner import Runner
import ansible_runner
import io 
import json
import contextlib
import yaml

class AnsibleRunTimeExecution(Exception):
    pass


class DataParsingException(Exception):
    pass 


class AnsibleWrapper:
    def __init__(self, inventory: dict, playbook: dict):
        self.inventory = inventory
        self.playbook = playbook
        self.event_data = dict()

    def run(self) -> None:
        redirected_stdout = io.StringIO()
        runner = Runner
        with contextlib.redirect_stdout(redirected_stdout):
            runner = ansible_runner.run(
                playbook=self.playbook,
                inventory=self.inventory,
                artifact_dir='logs/ansible-runner',
                json_mode=True
            )
        if runner.rc != 0:
            raise AnsibleRunTimeExecution("")
        json_data = self.construct_json_data(redirected_stdout)
        self.parse_json_data(json_data)
    
    def construct_json_data(self, stdout: io.StringIO) -> dict:
        constructed_stdout = ",".join(stdout.getvalue().strip().splitlines())
        constructed_json_string = ''.join(["[", constructed_stdout, "]"])
        try:
            json_data = json.loads(constructed_json_string)
            return json_data
        except:
            raise DataParsingException("")


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


if __name__ == '__main__':
    with open('tester.yaml', 'r') as f:
        playbook = yaml.safe_load(f)
    with open('inventory/tester.yaml', 'r') as f:
        inventory = yaml.safe_load(f)
    ansible_job = AnsibleWrapper(playbook=playbook, inventory=inventory)
    ansible_job.run()
