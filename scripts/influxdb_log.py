"""Script for logging to InfluxDB."""
import time
import sys
import json
import logging
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from urllib3.exceptions import ReadTimeoutError

import lakeshore


def main(config_file):
    """Query user for setup info and start logging to InfluxDB."""

    # read the config file
    with open(config_file, encoding='utf-8') as cfg_file:
        cfg = json.load(cfg_file)

    verbose = cfg['verbose'] == 1

    # Do we have a logfile?
    if cfg['logfile'] is not None:
        # log to a file
        logger = logging.getLogger(cfg['logfile'])
        if verbose:
            logger.setLevel(logging.DEBUG)
        else:
            logger.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler = logging.FileHandler(cfg['logfile'], mode='w')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    else:
        logger = None

    # Connect to Lakeshore
    if verbose:
        print("Connecting to Lakeshore...")
    if logger:
        logger.info('Connecting to InfluxDB...')
    lake = lakeshore.LakeshoreController()
    lake.set_connection(cfg['device_host'], cfg['device_port'])
    lake.connect()
    lake.initialize()

    # Get channels to log
    channels = cfg['log_channels']

    # Try/except to catch exceptions
    db_client = None
    try:
        # Loop until ctrl-C
        while True:
            try:
                # Connect to InfluxDB
                if verbose:
                    print("Connecting to InfluxDB...")
                if logger:
                    logger.info('Connecting to InfluxDB...')
                db_client = InfluxDBClient(url=cfg['db_url'], token=cfg['db_token'],
                                           org=cfg['db_org'])
                write_api = db_client.write_api(write_options=SYNCHRONOUS)

                # Loop over channels to log
                for chan in channels:
                    if chan in lake.sensors:
                        value = lake.get_temperature(chan)
                    elif chan in lake.outputs:
                        value = lake.get_heater_output(chan)
                    else:
                        print(f"ERROR: Unknown channel {chan}")
                        sys.exit(1)
                    point = (
                        Point("Lakeshore")
                        .field(channels[chan]['field'], value)
                        .tag("units", channels[chan]['units'])
                        .tag("channel", f"{cfg['db_channel']}")
                    )
                    write_api.write(bucket=cfg['db_bucket'], org=cfg['db_org'], record=point)
                    if verbose:
                        print(point)
                    if logger:
                        logger.debug(point)

                # Close db connection
                if verbose:
                    print("Closing connection to InfluxDB...")
                if logger:
                    logger.info('Closing connection to InfluxDB...')
                db_client.close()
                db_client = None

            # Handle exceptions
            except ReadTimeoutError as e:
                print(f"ReadTimeoutError: {e}, will retry.")
                if logger:
                    logger.critical("ReadTimeoutError: %s, will retry.", e)
            except Exception as e:
                print(f"Unexpected error: {e}, will retry.")
                if logger:
                    logger.critical("Unexpected error: %s, will retry.", e)

            # Sleep for interval_secs
            if verbose:
                print(f"Waiting {cfg['interval_secs']:d} seconds...")
            if logger:
                logger.info("Waiting %d seconds...", cfg['interval_secs'])
            time.sleep(cfg['interval_secs'])

    except KeyboardInterrupt:
        print("\nShutting down InfluxDB logging...")
        if logger:
            logger.critical("Shutting down InfluxDB logging...")
        if db_client:
            db_client.close()
        lake.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python influxdb_log.py <influxdb_log.json>")
        sys.exit(0)
    main(sys.argv[1])
