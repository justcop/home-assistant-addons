# GitHub Actions Cleanup

A one-shot Home Assistant add-on for deleting stored GitHub Actions artifacts and reclaiming Actions storage.

It deletes **artifacts only**. It does not delete source code, releases, workflow definitions, or workflow run history.

## Use

1. Install the add-on from this repository.
2. Set `repository` to the repository to clean, for example `justcop/glasto-switcher`.
3. Enter a GitHub token with permission to read and delete Actions artifacts for that repository.
4. Leave `confirm_delete: false` and start the add-on once. The log will show how many artifacts it found and their approximate total size.
5. Set `confirm_delete: true` and start it again to delete them.

The add-on is configured as `startup: once` and `boot: manual_only`, so it only runs when manually started.
