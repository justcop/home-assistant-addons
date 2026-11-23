# My Add-on Updater

Open **My Add-ons** in Home Assistant. Automatic compatible code updates are enabled by default. The interface shows installed image versions, deployed GitHub commits, update classification, timestamps and failures. Check now, redeploy a complete compatible SHA, or recover an interrupted deployment from the same page.

Application code is downloaded from `justcop/home-assistant-addons` only. Standard image updates use Home Assistant; release versions are generated automatically by GitHub Actions. No application credentials are copied into the updater. It has Supervisor administration permission to inspect and restart only its own discovered repository applications. It does not manage third-party add-ons.

Keep this app's `/data` in Home Assistant backups. Runtime code on `/media/storage/addon-code` is regenerable from GitHub and does not need to be backed up. Do not manually edit pointers or manifests. See `delivery/README.md` in the repository for the compatibility and recovery design.
