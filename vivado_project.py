import os
import logging
import shutil

from pyvivado import boards, tasks_collection, hash_helper
from pyvivado import params_helper, vivado_task

logger = logging.getLogger(__name__)


class VivadoProject(object):
    '''
    The base class for python wrappers around Vivado Projects.

    Also does some management of Vivado processes (`Task`s) that are run.
    '''

    def __init__(self, project, part=None, board=None, overwrite_ok=False):
        '''
        Create a new Vivado project.

        Args:
            `project`: A BaseProject that we want to create a vivado project based upon.
            `part`: The 'part' to use when implementing.
            `board`: The 'board' to used when implementing.

        Returns:
            A python `VivadoProject` object that wraps a Vivado project.
            The Vivado project itself will still be in the middle of being
            created when the function returns.
        '''
        logger.debug('Init vivado project.')
        self.project = project
        self.directory = self.directory_from_project(project)
        self.filename = os.path.join(self.directory, 'TheProject.xpr')
        self.new = not os.path.exists(self.directory)
        if not self.new:
            if not os.path.exists(self.filename):
                raise Exception('Directory exists, but project file does not.')
        params_fn = os.path.join(self.directory, 'params.txt')
        self.params_helper = params_helper.ParamsHelper(params_fn)
        old_params = self.params_helper.read()
        if old_params is not None:
            if part is None:
                part = old_params['part']
            if board is None:
                board = old_params['board']
        new_params = {
            'part': part,
            'board': board,
            }
        refresh = False
        if old_params is not None:
            if not old_params == new_params:
                if not overwrite_ok:
                    raise Exception('Part or Board have changed. {} -> {}'.format(old_params, new_params))
                else:
                    refresh = True
        else:
            if not self.new:
                raise Exception('No Part of Board parameters found for existing vivado project.')
        self.part = part
        self.board = board
        self.hash_helper = hash_helper.HashHelper(self.directory, self.project.get_hash)
        if (not self.new) and self.hash_helper.is_changed():
            if not overwrite_ok:
                raise Exception('Hash has changed in project but overwrite is not allowed.')
            else:
                refresh = True
        if refresh:
            shutil.rmtree(self.directory)
        self.tasks_collection = tasks_collection.TasksCollection(
            self.directory, task_type=vivado_task.VivadoTask)
        if self.new or refresh:
            os.mkdir(self.directory)
            self.params_helper.write(new_params)
            self.hash_helper.write()
            self.launch_create_task()

    @classmethod
    def directory_from_project(cls, project):
        return os.path.join(project.directory, 'vivado')

    @staticmethod
    def make_create_vivado_project_command(
            directory, design_files, simulation_files, ips, part, board,
            top_module):
        # Format the IP infomation into a TCL-friendly format.
        tcl_ips = []
        for ip_name, ip_properties, module_name in ips:
            ip_version = ''
            tcl_start = '{ip_name} {{{ip_version}}} {module_name}'.format(
                ip_name=ip_name, ip_version=ip_version,
                module_name=module_name)
            tcl_properties = ' '.join(
                ['{{ {} {} }}'.format(k, v) for k,v in ip_properties])
            tcl_ip = '{} {{ {} }}'.format(tcl_start, tcl_properties)
            tcl_ips.append(tcl_ip)
        tcl_ips = ' '.join(['{{ {} }}'.format(ip) for ip in tcl_ips])
        # Fail if a project already exists in this directory.
        if os.path.exists(os.path.join(directory, 'TheProject.xpr')):
            raise Exception('Vivado Project already exists.')
        # Write a hash that specifies the state of the files when the
        # Vivado project was created.
        if board in boards.params:
            board_params = boards.params[board]
            board_name = board_params['xilinx_name']
            part_name = board_params['part']
            assert(part is None)
        else:
            board_name = board
            part_name = part
        if board_name is None:
            board_name = ''
        if part_name is None:
            part_name = ''
        # Generate a TCL command to create the project.
        command_template = '''::pyvivado::create_vivado_project {{{directory}}} {{ {design_files} }}  {{ {simulation_files} }} {{{part}}} {{{board}}} {{{ips}}} {{{top_module}}}'''
        command = command_template.format(
            directory=directory,
            design_files=' '.join([
                '{'+f+'}' for f in design_files]),
            simulation_files=' '.join([
                '{'+f+'}' for f in simulation_files]),
            part=part_name,
            board=board_name,
            ips=tcl_ips,
            top_module=top_module,
        )
        return command

    @staticmethod
    def make_create_vivado_simset_command(
            directory, test_name, simulation_files):
        # Generate a TCL command to create the simset.
        command_template = '''::pyvivado::create_simset {{{directory}}} {{{test_name}}} {{ {simulation_files} }};'''
        command = command_template.format(
            directory=directory,
            test_name=test_name,
            simulation_files=' '.join([
                '{'+f+'}' for f in simulation_files]),
        )
        return command

    def launch_create_task(self):
        design_files = self.project.files_and_ip['design_files']
        simulation_files = self.project.files_and_ip['simulation_files']
        ips = self.project.files_and_ip['ips']
        top_module =self.project.files_and_ip['top_module']
        command = self.make_create_vivado_project_command(
            self.directory, design_files, simulation_files,
            ips, self.part, self.board, top_module)
        logger.debug('Command is {}'.format(command))
        logger.debug('Directory of new project is {}.'.format(self.directory))
        # Create a task to create the project.
        t = vivado_task.VivadoTask.create(
            collection=self.tasks_collection,
            description='Creating a new Vivado project.',
            command_text=command,
        )
        t.run()

    def utilization_file(self, from_synthesis=False):
        if from_synthesis:
            fn = 'synth_utilization.txt'
        else:
            fn = 'impl_utilization.txt'
        fn = os.path.join(self.directory, fn)
        return fn

    def power_file(self, from_synthesis=False):
        if from_synthesis:
            fn = 'synth_power.txt'
        else:
            fn = 'impl_power.txt'
        fn = os.path.join(self.directory, fn)
        return fn

    def get_power(self, from_synthesis=False, names=None):
        if names is None:
            names = ['Total']
        fn = self.power_file(from_synthesis=from_synthesis)
        pwers = {}
        with open(fn, 'r') as f:
            for line in f:
                bits = [s.strip() for s in line.split('|')]
                if (len(bits) == 7) and (bits[1] in names):
                    pwers[bits[1]] = float(bits[2])
        return pwers

    def get_utilization(self, from_synthesis=False):
        fn = self.utilization_file(from_synthesis=from_synthesis)
        if not os.path.exists(fn):
            t = self.generate_reports(from_synthesis=from_synthesis)
            t.wait()
            t.log_messages(t.get_messages())
        parents = []
        with open(fn, 'r') as f:
            found_hier = False
            for line in f:
                if not found_hier:
                    bits = [s.strip() for s in line.split('|')]
                    if (len(bits) > 1) and (bits[1] == 'Instance'):
                        categories = bits[3: -1]
                        found_hier = True
                else:
                    bits = line.split('|')
                    if len(bits) > 2:
                        hier_level = (len(bits[1]) - len(bits[1].lstrip()) - 1)//2
                        this_ut = {
                            'Instance': bits[1].strip(),
                            'Module': bits[2].strip(),
                            'children': [],
                        }
                        for index, category in enumerate(categories):
                            this_ut[category] = int(bits[index+3].strip())
                        if len(parents) == 0:
                            assert(hier_level == 0)
                            parents = [this_ut]
                        else:
                            parent = parents[hier_level-1]
                            parent['children'].append(this_ut)
                            parents = parents[:hier_level] + [this_ut]
        return parents[0]

    def synthesize(self, keep_hierarchy=False):
        '''
        Spawn a Vivado process to synthesize the project.
        '''
        if keep_hierarchy:
            command_templ = '::pyvivado::open_and_synthesize {{{}}} "keep_hierarchy"'
        else:
            command_templ = '::pyvivado::open_and_synthesize {{{}}} {{}}'
        t = vivado_task.VivadoTask.create(
            parent_directory=self.directory,
            command_text=command_templ.format(self.directory),
            description='Synthesize project.',
            tasks_collection=self.tasks_collection,
        )
        t.run()
        return t

    def implement(self):
        '''
        Spawn a Vivado process to implement the project.
        '''
        t = vivado_task.VivadoTask.create(
            parent_directory=self.directory,
            command_text='::pyvivado::open_and_implement {{{}}}'.format(
                self.directory),
            description='Implement project.',
            tasks_collection=self.tasks_collection,
        )
        t.run()
        return t

    def generate_reports(self, from_synthesis=False):
        '''
        Spawn a Vivado process to generate reports
        '''
        if from_synthesis:
            command_templ = '::pyvivado::generate_synth_reports {{{}}}'
        else:
            command_templ = '::pyvivado::generate_impl_reports {{{}}}'
        t = vivado_task.VivadoTask.create(
            parent_directory=self.directory,
            command_text=command_templ.format(self.directory),
            description='Generate reports.',
            tasks_collection=self.tasks_collection,
        )
        t.run()
        return t

    def run_simulation(self, test_name, runtime, sim_type='hdl'):
        '''
        Spawns a vivado process that will run a simulation of the project.

        Args:
            `runtime`: A string specifying the runtime.
            'sim_type`: The string specifying the simulation type.  It can be
               'hdl', 'post_synthesis', or 'timing.

        Returns a (errors, output_data) tuple where:
            `errors`: If a list of errors produced by the simulation task.
            `output_data`: A list of dictionaries of the output wire values.
        '''
        simulation_files = self.project.file_helper.read()['simulation_files']
        command_template = '''
open_project {{{project_filename}}}
::pyvivado::run_{sim_type}_simulation {{{directory}}} {{{test_name}}} {{{runtime}}} {{ {simulation_files} }}
'''
        command = command_template.format(
            project_filename=self.filename, runtime=runtime, sim_type=sim_type,
            test_name=test_name, directory=self.directory,
            simulation_files=' '.join([
                '{'+f+'}' for f in simulation_files]),
            )
        # Create a task to run the simulation.
        t = vivado_task.VivadoTask.create(
            collection=self.tasks_collection,
            description='Running a HDL simulation.',
            command_text=command,
        )
        # Run the simulation task and wait for it to complete.
        t.run_and_wait()
        errors = t.get_errors()
        output_filename = self.project.get_output_filename(
            sim_type='vivado_'+sim_type, test_name=test_name)
        if not os.path.exists(output_filename):
            logger.error('Failed to create output file from simulation')
            data_out = []
        else:
            # Read the output files.
            data_out = self.project.interface.read_output_file(
                os.path.join(t.directory, output_filename))
        return errors, data_out