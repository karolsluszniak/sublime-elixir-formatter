import os
import sublime
import sublime_plugin
import subprocess
import threading
import re

SYNTAX_ERROR_RE = re.compile(
    r"^\*\*\s\((.+)\)\s(.+)\:(\d+)\:\s(.+)$",
    re.MULTILINE | re.IGNORECASE | re.UNICODE)
PLUGIN_NAME = "ElixirFormatter"
PLUGIN_CMD_NAME = "elixir_formatter_format_file"

class ElixirFormatter:
    @staticmethod
    def run(view, edit, file_name):
        project_root_with_mix = ElixirFormatter.find_project(file_name)
        project_root = project_root_with_mix or os.path.dirname(file_name)
        file_name_rel = file_name.replace(project_root + "/", "")
        blacklisted = ElixirFormatter.check_blacklisted_in_config(project_root, file_name_rel)
        if blacklisted:
            print("{0} skipped '{1}' due to :inputs key in '.formatter.exs'".
              format(PLUGIN_NAME, file_name_rel))
            return

        region = sublime.Region(0, view.size())
        source_text = view.substr(region)
        result = ElixirFormatter.mix_format(source_text, project_root)
        if result.is_successful:
            if result.is_equal(source_text):
                print("{0} found no formatting is needed: files are equal".
              format(PLUGIN_NAME, file_name_rel))
                return
            previous_position = view.viewport_position()
            Utils.replace(view, edit, region, result.pretty_text)
            Utils.indent(view)
            Utils.restore_position(view, previous_position)
            Utils.st_status_message("file formatted")
        else:
            print("{0}: {1}".format(PLUGIN_NAME, result.error.full_message))
            view.run_command("goto_line", {"line": result.error.line})
            Utils.st_status_message(result.error.status_message)

    @staticmethod
    def find_project(cwd = None):
        cwd = cwd or os.getcwd()
        if cwd == os.path.realpath('/'):
            return None
        elif os.path.exists(os.path.join(cwd, 'mix.exs')):
            return cwd
        else:
            return ElixirFormatter.find_project(os.path.dirname(cwd))

    @staticmethod
    def mix_format(source_text, project_root):
        settings = sublime.load_settings('Preferences.sublime-settings')
        env = os.environ.copy()

        try:
            env['PATH'] = os.pathsep.join([settings.get('env')['PATH'], env['PATH']])
        except (TypeError, ValueError, KeyError):
            pass

        if sublime.platform() == "windows":
            launcher = ["mix.bat", "format", "-"]
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        else:
            launcher = ["mix", "format", "-"]
            startupinfo = None

        process = subprocess.Popen(
            launcher,
            cwd = project_root,
            env = env,
            stdin = subprocess.PIPE,
            stdout = subprocess.PIPE,
            stderr = subprocess.PIPE,
            startupinfo = startupinfo)

        binary_source = bytes(source_text, "utf-8")
        stdout, stderr = process.communicate(input = binary_source)
        stdout = stdout.decode('utf-8')
        stderr = stderr.decode('utf-8')
        exit_code = process.returncode

        return MixFormatResult(stdout, stderr, exit_code)

    @staticmethod
    def run_command(project_root, task_args):
        settings = sublime.load_settings('Preferences.sublime-settings')
        env = os.environ.copy()

        try:
            env['PATH'] = os.pathsep.join([settings.get('env')['PATH'], env['PATH']])
        except (TypeError, ValueError, KeyError):
            pass

        if sublime.platform() == "windows":
            launcher = ['cmd', '/c']
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        else:
            launcher = []
            startupinfo = None

        process = subprocess.Popen(
            launcher + task_args,
            cwd = project_root,
            env = env,
            stdout = subprocess.PIPE,
            stderr = subprocess.PIPE,
            startupinfo = startupinfo)

        stdout, stderr = process.communicate()
        stdout = stdout.decode('utf-8')
        stderr = stderr.decode('utf-8')

        return [stdout, stderr]

    check_blacklisted_script_template = """
      file = \"[[file]]\"
      formatter = \".formatter.exs\"
      with true <- File.exists?(formatter),
           {formatter_opts, _} <- Code.eval_file(formatter),
           {:ok, inputs} <- Keyword.fetch(formatter_opts, :inputs) do
        IO.puts("Check result: #{file in Enum.flat_map(inputs, &Path.wildcard/1)}")
      end
    """

    @staticmethod
    def check_blacklisted_in_config(project_root, file_name):
        if not os.path.isfile(os.path.join(project_root, ".formatter.exs")):
            return

        script = ElixirFormatter.check_blacklisted_script_template.replace("[[file]]", file_name)
        stdout, stderr = ElixirFormatter.run_command(project_root, ["elixir", "-e", script])
        return "Check result: false" in stdout

class ElixirFormatterFormatFileCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        file_name = self.view.file_name()
        extension = os.path.splitext(file_name)[1][1:]
        syntax = self.view.settings().get("syntax")
        if extension in ["ex", "exs"] or "Elixir" in syntax:
            ElixirFormatter.run(self.view, edit, file_name)

class ElixirFormatterEventListeners(sublime_plugin.EventListener):
    @staticmethod
    def on_pre_save(view):
        view.run_command(PLUGIN_CMD_NAME)

class MixFormatError:
    def __init__(self, text):
        self.text = text
        self.__matches = SYNTAX_ERROR_RE.search(text)

    @property
    def full_message(self):
        return self.__matches.group(0)

    @property
    def exception(self):
        return self.__matches.group(1)

    @property
    def line(self):
        return int(self.__matches.group(3))

    @property
    def reason(self):
        return self.__matches.group(4)

    @property
    def status_message(self):
        return "{0} - {1}".format(self.exception, self.reason)

class MixFormatResult:
    def __init__(self, stdout, stderr, exit_code):
        self.__stdout = stdout
        self.__stderr = stderr
        self.__exit_code = exit_code
        self.__error = None
        if not self.is_successful:
            self.__error = MixFormatError(self.__stderr)

    @property
    def is_successful(self):
        return self.__exit_code == 0

    @property
    def error(self):
        return self.__error

    def is_changed(self, source_text):
        trimmed_pretty = Utils.trim_trailing_ws_and_lines(self.pretty_text)
        trimmed_source = Utils.trim_trailing_ws_and_lines(source_text)
        return trimmed_source != source_text and trimmed_source != trimmed_pretty

    def is_equal(self, text):
        return not self.is_changed(text)

    @property
    def pretty_text(self):
        return self.__stdout

class Utils:
    @staticmethod
    def trim_trailing_ws_and_lines(val):
        if val is None:
            return val
        val = re.sub(r'\s+\Z', '', val)
        return val

    @staticmethod
    def indent(view):
        view.run_command('detect_indentation')

    @staticmethod
    def restore_position(view, previous_position):
        view.set_viewport_position((0, 0), False)
        view.set_viewport_position(previous_position, False)

    @staticmethod
    def replace(view, edit, region, text):
        view.replace(edit, region, text)

    @staticmethod
    def st_status_message(msg):
        sublime.set_timeout(lambda: sublime.status_message('{0}: {1}'.format(PLUGIN_NAME, msg)), 0)
