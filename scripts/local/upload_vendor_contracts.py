from demo_environment import create_workspace_client, upload_baseline_files


def main():
    upload_baseline_files(create_workspace_client())


if __name__ == "__main__":
    main()