import argparse
import time
import sys
import os
import logging

from datetime import date, datetime

from withings_sync.withings2 import Withings
from withings_sync.withings2 import WithingsAccount
from withings_sync.garmin import GarminConnect
from withings_sync.trainerroad import TrainerRoad
from withings_sync.fit import FitEncoder_Weight


def get_args():
    parser = argparse.ArgumentParser(
        description=('A tool for synchronisation of Withings '
                     '(ex. Nokia Health Body) to Garmin Connect'
                     ' and Trainer Road.')
    )

    def date_parser(s):
        return datetime.strptime(s, '%Y-%m-%d')

    parser.add_argument('--withings-user', '--wu',
                         default=os.environ.get('WITHINGS_USER'),
                         type=str,
                         metavar='WITHINGS_USER',
                         help='path to user json data file to login Withings.')

    parser.add_argument('--garmin-username', '--gu',
                        default=os.environ.get('GARMIN_USERNAME'),
                        type=str,
                        metavar='GARMIN_USERNAME',
                        help='username to login Garmin Connect.')
    parser.add_argument('--garmin-password', '--gp',
                        default=os.environ.get('GARMIN_PASSWORD'),
                        type=str,
                        metavar='GARMIN_PASSWORD',
                        help='password to login Garmin Connect.')

    parser.add_argument('--trainerroad-username', '--tu',
                        default=os.environ.get('TRAINERROAD_USERNAME'),
                        type=str,
                        metavar='TRAINERROAD_USERNAME',
                        help='username to login TrainerRoad.')
    parser.add_argument('--trainerroad-password', '--tp',
                        default=os.environ.get('TRAINERROAD_PASSWORD'),
                        type=str,
                        metavar='TRAINERROAD_PASSWORD',
                        help='username to login TrainerRoad.')

    parser.add_argument('--fromdate', '-f',
                        type=date_parser,
                        metavar='DATE')
    parser.add_argument('--todate', '-t',
                        type=date_parser,
                        default=date.today(),
                        metavar='DATE')

    parser.add_argument('--no-upload',
                        action='store_true',
                        help=('Won\'t upload to Garmin Connect and '
                              'output binary-strings to stdout.'))
    parser.add_argument('--verbose', '-v',
                        action='store_true',
                        help='Run verbosely')

    return parser.parse_args()


def sync(withings_user,
         garmin_username, garmin_password,
         trainerroad_username, trainerroad_password,
         fromdate, todate,
         no_upload, verbose):


    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        stream=sys.stdout)

    # Withings API
    if withings_user:
         withings = WithingsAccount(withings_user)
    else:
         withings = WithingsAccount(Withings.USER_CONFIG)

    if not fromdate:
        startdate = withings.getLastSync()
    else:
        startdate = int(time.mktime(fromdate.timetuple()))

    enddate = int(time.mktime(todate.timetuple())) + 86399
    logging.info("Fetching measurements from %s to %s", time.strftime('%Y-%m-%d %H:%M', time.gmtime(startdate)), time.strftime('%Y-%m-%d %H:%M', time.gmtime(enddate)))

    height = withings.getHeight()

    groups = withings.getMeasurements(startdate=startdate, enddate=enddate)

    # Only upload if there are measurement returned
    if groups is None or len(groups) == 0:
        logging.error('No measurements to upload for date or period specified')
        return -1

    # Create FIT file
    logging.debug('Generating fit file...')
    fit = FitEncoder_Weight()
    fit.write_file_info()
    fit.write_file_creator()

    last_dt = None
    last_weight = None

    for group in groups:
        # Get extra physical measurements
        dt = group.get_datetime()
        weight = group.get_weight()
        fat_ratio = group.get_fat_ratio()
        muscle_mass = group.get_muscle_mass()
        hydration = group.get_hydration()
        bone_mass = group.get_bone_mass()
        raw_data = group.get_raw_data()

        if weight is None:
            logging.info('This Withings metric contains no weight data.  Not syncing...')
            logging.debug('Detected data: ')
            for dataentry in raw_data:
                logging.debug(dataentry)
            continue

        if height and weight:
            bmi = round(weight / pow(height, 2), 1)
        else:
            bmi = None

        if hydration and weight:
            percent_hydration = hydration * 100.0 / weight
        else:
            percent_hydration = None

        fit.write_device_info(timestamp=dt)
        fit.write_weight_scale(timestamp=dt,
                               weight=weight,
                               percent_fat=fat_ratio,
                               percent_hydration=percent_hydration,
                               bone_mass=bone_mass,
                               muscle_mass=muscle_mass,
                               bmi=bmi)

        logging.debug('Record: %s weight=%s kg, '
                      'fat_ratio=%s %%, '
                      'muscle_mass=%s kg, '
                      'hydration=%s %%, '
                      'bone_mass=%s kg, '
                      'bmi=%s',
                      dt, weight, fat_ratio,
                      muscle_mass, hydration,
                      bone_mass, bmi)

        if last_dt is None or dt > last_dt:
            last_dt = dt
            last_weight = weight

    fit.finish()

    if last_weight is None:
        logging.error('Invalid weight')
        return -1

    if no_upload:
        sys.stdout.buffer.write(fit.getvalue())
        return 0

    # Upload to Trainer Road
    if trainerroad_username:
        logging.info('Trainerroad username set -- attempting to sync')
        logging.info(' Last weight {}'.format(last_weight))
        logging.info(' Measured {}'.format(last_dt))

        tr = TrainerRoad(trainerroad_username, trainerroad_password)
        tr.connect()

        logging.info(f'Current TrainerRoad weight: {tr.weight} kg ')
        logging.info(f'Updating TrainerRoad weight to {last_weight} kg')

        tr.weight = round(last_weight, 1)
        tr.disconnect()

        logging.info('TrainerRoad update done!')
    else:
        logging.info('No Trainerroad username or a new measurement '
                     '- skipping sync')

    # Upload to Garmin Connect
    if garmin_username:
        garmin = GarminConnect()
        session = garmin.login(garmin_username, garmin_password)
        logging.debug('attempting to upload fit file...')
        r = garmin.upload_file(fit.getvalue(), session)
        if r:
            # Save this sync so we don't re-download the same data again (if no range has been specified)
            if not fromdate:
                withings.setLastSync()

            logging.info('Fit file uploaded to Garmin Connect')
    else:
        logging.info('No Garmin username - skipping sync')


def main():
    args = get_args()

    sync(**vars(args))
