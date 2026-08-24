{ pkgs, lib, ... }:

{
  languages.python = {
    enable = true;
    uv.enable = true;
    venv.enable = true;
  };

  # Install fleetman (and dev deps) editable into the venv.
  languages.python.venv.requirements = ''
    -e .[dev]
  '';

  scripts.fleet.exec = "fleetman \"$@\"";

  # devman — the automation plane (CONCEPT.md §5). `base` alone, and the direct
  # task shape (like nix-desktop): this repository declares no linter, so
  # `base:check` is the honest fast check — a stdlib compile of the source —
  # and `base:test` is the suite `enterTest` already runs.
  devman = {
    enable = true;
    project = "fleetman";
    groups = [ "base" ];
  };

  # https://devenv.sh/tasks/
  #
  # `base:test` names `uv run pytest`, not bare `pytest`: the venv bin is on the
  # interactive shell's PATH but not on the task runner's PATH, and a bare name
  # fails there with `command not found` (STAGE_7_LOG.md, wave 2b).
  tasks = {
    "base:check".exec = "python -m compileall -q src";
    "base:test".exec = "uv run pytest -q";
  };

  enterTest = ''
    pytest -q
  '';
}
