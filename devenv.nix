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

  enterTest = ''
    pytest -q
  '';
}
