# Windows updater compatibility and manual migration

## Compatibility boundary

The Windows updater validates the ZIP before changing the installation. Each installation runs the `apply_update.ps1` that shipped with it; replacing that script inside a new archive cannot help if the old script rejects the archive first.

The published v2.0.1 updater accepts its fixed list of root-level application files and the two supported `data/` files. It rejects package directories such as `cad/`, `engineering/`, `equipment/`, `hap/`, `integration/`, `reports/`, and `ui/`. Some pre-release updater builds used a different fixed allow-list and required a fixed set of CAD files; those builds also cannot be assumed to accept newer package layouts.

A release containing only the root files and supported `data/` files can be applied by the published v2.0.1 updater. Once a release includes source under any package directory, older installations cannot safely self-update through that old helper. Treat the first such release as a manual migration for those installations; announce this limitation in its release notes. After migration, the new updater accepts Python files under the explicitly supported package directories for subsequent updates.

Do not claim support for automatic updates from older installations just because the new updater and PowerShell script accept the package. The old installed script is the first validator on that path.

## Manual migration procedure

Use this procedure if an automatic update reports an unexpected archive entry, a missing required file, or otherwise fails validation:

1. Close Carrier AC Selector. Do not repeatedly retry the rejected archive.
2. Make a separate backup of the complete existing application folder, including `data/`, and note any project files stored elsewhere. Keep the backup until the new installation has been verified.
3. Download the complete `CarrierACSelector-Windows.zip` asset from the intended GitHub Release and extract it into a **new, empty folder**. Do not extract over the old folder; stale modules or the old `.venv` can mask missing files and create a mixed installation.
4. Copy the user-owned `data/Carrier_AC_Database.xlsx` and/or `data/Carrier_AC_Database.csv` from the backup into the new installation's `data/` folder if those files exist. Do not overwrite them with the release's sample database.
5. From the new folder, run `Start_Carrier.bat` and allow setup to create a fresh Windows `.venv` and install dependencies. Keep the folder at its final location after setup because the virtual environment and shortcut are path-specific.
6. Verify the application starts, the expected version is shown, the equipment database opens, and any externally stored project files remain accessible. Only then may the old folder be retired.

If setup or startup fails, retain both folders and the update log at `%LOCALAPPDATA%\CarrierACSelector\update.log` and restore access to the previous installation from its backup. Do not delete the backup as part of migration.

## Release gate

Before publishing a release, the Windows release workflow must:

- compile root Python modules and recursive package sources using explicit PowerShell file enumeration;
- validate the generated package with the current Python and PowerShell validators;
- start from a clean historical v2.0.1 installation and apply an in-place update through its shipped updater when the archive remains compatible, then check imports and GUI startup;
- when package directories make that historical updater incompatible, exercise the documented clean-folder installation path and then apply a real update through the new updater, checking imports and GUI startup;
- fail publication if the workflow's selected path cannot validate the archive, install dependencies, import the packaged modules, or complete the application startup health check.

When adding package directories outside a legacy updater's whitelist, either keep the manual-migration notice for those installations and adjust the release gate to test that documented transition, or provide and test a separately distributed bootstrap installer before claiming in-app upgrade support.
