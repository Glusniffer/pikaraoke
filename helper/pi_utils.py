import os

raspi_wifi_config_ip = "10.0.0.1"
raspi_wifi_conf_file = "/etc/raspiwifi/raspiwifi.conf"
raspi_wifi_config_installed = os.path.exists(raspi_wifi_conf_file)


def get_raspi_wifi_conf_vals(self):
    """Extract values from the RaspiWiFi configuration file."""
    f = open(raspi_wifi_conf_file, "r")

    # Define default values.
    #
    # References:
    # - https://github.com/jasbur/RaspiWiFi/blob/master/initial_setup.py (see defaults in input prompts)
    # - https://github.com/jasbur/RaspiWiFi/blob/master/libs/reset_device/static_files/raspiwifi.conf
    #
    server_port = "80"
    ssid_prefix = "RaspiWiFi Setup"
    ssl_enabled = "0"

    # Override the default values according to the configuration file.
    for line in f.readlines():
        if "server_port=" in line:
            server_port = line.split("t=")[1].strip()
        elif "ssid_prefix=" in line:
            ssid_prefix = line.split("x=")[1].strip()
        elif "ssl_enabled=" in line:
            ssl_enabled = line.split("d=")[1].strip()

    return (server_port, ssid_prefix, ssl_enabled)
