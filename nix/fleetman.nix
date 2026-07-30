# Reusable devenv module: fleetman workspace indexing.
#
# Unlike the per-repo managers, fleetman operates on the *workspace* (the parent
# directory whose children are repos). Import it from the workspace-root devenv,
# or from any repo whose devenv should expose the fleet map:
#
#   imports = [ ./nix/fleetman.nix ];
#
# Assumes `fleetman` is on PATH via the devenv Python venv
# (languages.python.venv + uv). Tasks default the workspace root to the parent of
# DEVENV_ROOT; override with FLEETMAN_ROOT.
{ config, ... }:

let
  venvBin = "${config.devenv.state}/venv/bin";
  # Workspace root = parent of the repo, unless FLEETMAN_ROOT is set.
  root = ''"''${FLEETMAN_ROOT:-$(dirname "$DEVENV_ROOT")}"'';
in
{
  tasks = {
    "fleetman:index".exec = ''${venvBin}/fleetman index --root ${root}'';
    "fleetman:graph".exec = ''${venvBin}/fleetman graph --root ${root}'';
    "fleetman:doctor".exec = ''${venvBin}/fleetman doctor --root ${root}'';
    # Dry-run reconcile of the declared repo set (repos.toml) vs. what's on disk.
    # Add `--apply` manually to actually clone/fetch — kept out of the task so a
    # bare `devenv tasks run fleetman:sync` never mutates the filesystem.
    "fleetman:sync".exec = ''${venvBin}/fleetman sync --root ${root}'';
  };
}
