"""Script for logging to InfluxDB."""
import time
import sys
import json
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
import lakeshore


def main(config_file):
    """Query user for setup info and start logging to InfluxDB."""

    # read the config file
    with open(config_file, encoding='utf-8') as cfg_file:
        cfg = json.load(cfg_file)

    verbose = cfg['verbose'] == 1

    # Connect to Lakeshore
    if verbose:
        print("Connecting to Lakeshore...")
    lake = lakeshore.LakeshoreController()
    lake.set_connection(cfg['device_host'], cfg['device_port'])
    lake.connect()
    lake.initialize()

    # Connect to InfluxDB
    if verbose:
        print("Connecting to InfluxDB...")
    db_client = InfluxDBClient(url=cfg['db_url'], token=cfg['db_token'], org=cfg['db_org'])
    write_api = db_client.write_api(write_options=SYNCHRONOUS)

    try:
        channels = cfg['log_channels']
        while True:
            for chan in channels:
                if chan in lake.sensors:
                    value = lake.get_temperature(chan)
                elif chan in lake.outputs:
                    value = lake.get_heater_output(chan)
                else:
                    print(f"ERROR: Unknown channel {chan}")
                    sys.exit(1)
                point = (
                    Point("srs_ptc10")
                    .field(channels[chan]['field'], value)
                    .tag("units", channels[chan]['units'])
                    .tag("channel", f"{cfg['db_channel']}")
                )
                write_api.write(bucket=cfg['db_bucket'], org=cfg['db_org'], record=point)
                if verbose:
                    print(point)

            # Sleep for interval_secs
            time.sleep(cfg['interval_secs'])

    except KeyboardInterrupt:
        print("\nShutting down InfluxDB logging...")
        db_client.close()
        lake.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python influxdb_log.py <influxdb_log.json>")
        sys.exit(0)
    main(sys.argv[1])
