vq configuration examples

Starting points for ~/.config/vq/config.toml on each machine that takes part
in submitting calculations from vibe-view. The chapter "Clusters and remote
machines" explains every block; "Supported programs and calculations" explains
the program registrations.

  workstation.toml    a machine with no batch system: the vq daemon runs the
                      jobs, and the viewer's submit buttons use it
  laptop.toml         a client that submits to that workstation over SSH
  driver.toml         the always-on daemon host that drives a SLURM and a
                      PBS/Torque cluster
  cluster-login.toml  the programs registered on a cluster login node

Every host name is an alias from ~/.ssh/config, and every path is a
placeholder: replace USER, the install paths, accounts, partitions and queues
with your own. Merge the blocks you need into an existing config.toml rather
than replacing it, then run "vq daemon reload" and "vq programs HOST".

These files were written for the vibe-view project and are distributed under
the Mozilla Public License 2.0 with vibe-view.
