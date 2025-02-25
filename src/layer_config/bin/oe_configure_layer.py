#!/bin/env python

# Copyright (c) 2002-2017, California Institute of Technology.
# All rights reserved.  Based on Government Sponsored Research under contracts NAS7-1407 and/or NAS7-03001.
#
# Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:
#   1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
#   2. Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.
#   3. Neither the name of the California Institute of Technology (Caltech), its operating division the Jet Propulsion Laboratory (JPL),
#      the National Aeronautics and Space Administration (NASA), nor the names of its contributors may be used to
#      endorse or promote products derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
# INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
# IN NO EVENT SHALL THE CALIFORNIA INSTITUTE OF TECHNOLOGY BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
# STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE,
# EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

#
# oe_configure_layer.py
# The OnEarth Layer Configurator.
#
#
# Example XML configuration file:
#
'''
<?xml version="1.0" encoding="UTF-8"?>
<LayerConfiguration>
 <Identifier>MODIS_Aqua_Cloud_Top_Temp_Night</Identifier>
 <Title>MODIS AQUA Nighttime Cloud Top Temperature</Title>
 <FileNamePrefix>MYR6CTTLLNI</FileNamePrefix>
 <TiledGroupName>MODIS AQUA Nighttime Cloud Top Temperature tileset</TiledGroupName>
 <Compression>PNG</Compression>
 <TileMatrixSet>EPSG4326_2km</TileMatrixSet>
 <EmptyTileSize offset="0">1397</EmptyTileSize>
 <Projection>EPSG:4326</Projection>
 <EnvironmentConfig>/layer_config/conf/environment_geographic.xml</EnvironmentConfig>
 <ArchiveLocation static="false" year="true">/data/EPSG4326/MYR6CTTLLNI</ArchiveLocation>
 <ColorMap>http://localhost/colormap/sample.xml</ColorMap>
 <Time>DETECT</Time>
 <Time>2014-04-01/DETECT/P1D</Time>
</LayerConfiguration>
'''
#
# Global Imagery Browse Services
# NASA Jet Propulsion Laboratory

import os
import subprocess
import sys
import urllib
import urllib2
import xml.dom.minidom
import logging
import shutil
import re
import distutils.spawn
import sqlite3
import glob
import json
from datetime import datetime, timedelta
from time import asctime, time as tm
from dateutil.relativedelta import relativedelta
from optparse import OptionParser
from lxml import etree
import cgi
from oe_configure_reproject_layer import build_reproject_configs, make_gdal_tms_xml, \
    WMS_LAYER_GROUP_TEMPLATE, DIMENSION_TEMPLATE, VALIDATION_TEMPLATE, STYLE_TEMPLATE, MAPFILE_TEMPLATE
from oe_configure_remote_layers import get_remote_layers
from oe_utils import Environment, get_environment, sigevent, log_info_mssg, log_info_mssg_with_timestamp, log_the_command, bulk_replace

reload(sys)
sys.setdefaultencoding('utf8')

versionNumber = '1.3.8'
current_conf = None


class WMTSEndPoint:
    """End point data for WMTS"""

    def __init__(self, path, cacheConfigLocation, cacheConfigBasename,
                 getCapabilities, projection):
        self.path = path
        self.cacheConfigLocation = cacheConfigLocation
        self.cacheConfigBasename = cacheConfigBasename
        self.getCapabilities = getCapabilities
        self.projection = projection


class TWMSEndPoint:
    """End point data for TWMS"""

    def __init__(self, path, cacheConfigLocation, cacheConfigBasename,
                 getCapabilities, getTileService, projection):
        self.path = path
        self.cacheConfigLocation = cacheConfigLocation
        self.cacheConfigBasename = cacheConfigBasename
        self.getCapabilities = getCapabilities
        self.getTileService = getTileService
        self.projection = projection


class WMSEndPoint:
    """End point data for WMS"""

    def __init__(self, mapfileStagingLocation, mapfileLocation,
                 mapfileLocationBasename, mapfileConfigLocation,
                 mapfileConfigBasename):
        self.mapfileStagingLocation = mapfileStagingLocation
        self.mapfileLocation = mapfileLocation
        self.mapfileLocationBasename = mapfileLocationBasename
        self.mapfileConfigLocation = mapfileConfigLocation
        self.mapfileConfigBasename = mapfileConfigBasename


class Projection:
    """Projection information for layer"""

    def __init__(self, projection_id, projection_wkt, projection_bbox,
                 projection_tilematrixsets, projection_tilematrixset_xml,
                 projection_lowercorner, projection_uppercorner):
        self.id = projection_id
        self.wkt = projection_wkt
        self.bbox_xml = projection_bbox
        self.tilematrixsets = projection_tilematrixsets  #returns TileMatrixSetMeta
        self.tilematrixset_xml = projection_tilematrixset_xml
        self.lowercorner = projection_lowercorner
        self.uppercorner = projection_uppercorner


class TileMatrixSetMeta:
    """TileMatrixSet metadata for WMTS"""

    def __init__(self, levels, scale):
        self.levels = levels
        self.scale = scale


warnings = []
errors = []


def log_sig_warn(mssg, sigevent_url):
    """
    Send a warning to the log and to sigevent.
    Arguments:
        mssg -- 'message for operations'
        sigevent_url -- Example:  'http://[host]/sigevent/events/create'
    """
    # Send to log.
    logging.warning(asctime() + " " + mssg)
    global warnings
    warnings.append(asctime() + " " + mssg)
    # Send to sigevent.
    try:
        sigevent('WARN', mssg, sigevent_url)
    except urllib2.URLError:
        print 'sigevent service is unavailable'


def log_sig_err(mssg, sigevent_url):
    """
    Send a warning to the log and to sigevent.
    Arguments:
        mssg -- 'message for operations'
        sigevent_url -- Example:  'http://[host]/sigevent/events/create'
    """
    # Send to log.
    logging.error(asctime() + " " + mssg)
    global errors
    errors.append(asctime() + " " + mssg)
    # Send to sigevent.
    try:
        sigevent('ERROR', mssg, sigevent_url)
    except urllib2.URLError:
        print 'sigevent service is unavailable'


def log_sig_exit(type, mssg, sigevent_url):
    """
    Send a message to the log, to sigevent, and then exit.
    Arguments:
        type -- 'INFO', 'WARN', 'ERROR'
        mssg -- 'message for operations'
        sigevent_url -- Example:  'http://[host]/sigevent/events/create'
    """
    # Add "Exiting" to mssg.
    mssg = str().join([mssg, '  Exiting oe_configure_layer.'])
    # Send to sigevent.
    try:
        sigevent(type, mssg, sigevent_url)
    except urllib2.URLError:
        print 'sigevent service is unavailable'
    # Send to log.
    if type == 'INFO':
        log_info_mssg_with_timestamp(mssg)
    elif type == 'WARN':
        logging.warning(asctime())
        logging.warning(mssg)
    elif type == 'ERROR':
        logging.error(asctime())
        logging.error(mssg)
    # Exit.
    sys.exit()


def get_dom_tag_value(dom, tag_name):
    """
    Return value of a tag from dom (XML file).
    Arguments:
        tag_name -- name of dom tag for which the value should be returned.
    """
    tag = dom.getElementsByTagName(tag_name)
    value = tag[0].firstChild.nodeValue.strip()
    return value


def change_dom_tag_value(dom, tag_name, value):
    """
    Return value of a tag from dom (XML file).
    Arguments:
        tag_name -- name of dom tag for which the value should be returned.
        value -- the replacement value.
    """
    tag = dom.getElementsByTagName(tag_name)
    tag[0].firstChild.nodeValue = value


def run_command(cmd, sigevent_url):
    """
    Runs the provided command on the terminal.
    Arguments:
        cmd -- the command to be executed.
    """
    print '\nRunning command: ' + cmd
    process = subprocess.Popen(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    process.wait()
    for output in process.stdout:
        print output.strip()
    for error in process.stderr:
        log_sig_err(error.strip(), sigevent_url)
        raise Exception(error.strip())


def add_trailing_slash(directory_path):
    """
    Add trailing slash if one is not already present.
    Argument:
        directory_path -- path to which trailing slash should be confirmed.
    """
    # Add trailing slash.
    if directory_path[-1] != '/':
        directory_path = str().join([directory_path, '/'])
    # Return directory_path with trailing slash.
    return directory_path


def get_pretty_xml(xml_dom):
    """
    Formats an XML document into a string with nice-looking line-breaks (for get_mrf).
    """
    parser = etree.XMLParser(strip_cdata=False)
    xml = etree.fromstring(xml_dom.toxml(), parser)
    pretty_xml = etree.tostring(xml, pretty_print=True)
    return pretty_xml


def delete_mapfile_layer(mapfile, layerName):
    """
    Deletes a LAYER entry from a Mapfile.
    """
    mapfile.seek(0)
    endTagCount = None
    bytePosition = 0
    layerFound = False
    for line in mapfile.readlines():
        # Record byte position of LAYER tag in case we're about to find that it's a dupe
        if 'layer' in line.lower():
            layerStart = bytePosition
        # If this is a duplicate tag, start counting END tags
        if all(tag in line.lower() for tag in ('name', identifier)):
            endTagCount = 1
        # Increment the END count if additional tags that require an END appear
        if endTagCount > 0 and any(keyword in line.lower()
                                   for keyword in ('validation', 'projection',
                                                   'metadata')):
            endTagCount += 1
        # Decrement the END count each time an END tag is found
        if endTagCount > 0 and "end" in line.lower():
            endTagCount -= 1
        # Increment the overall file position
        bytePosition += len(line)
        # When last END tag is found, record the position of the final line and push LAYER start and end positions to list
        if endTagCount == 0:
            mapfile.seek(bytePosition)
            remainder = mapfile.read()
            mapfile.seek(layerStart)
            mapfile.truncate()
            mapfile.write(remainder)
            layerFound = True
            break
    return layerFound


def get_archive(archive_root, archive_configuration):
    """
    Gets archive location from an archive configuration file based on the archive root ID.
    Arguments:
        archive_root -- the key used for the archive
        archive_configuration -- the location of the archive configuration file
    """
    try:
        # Open file.
        archive_config = open(archive_configuration, 'r')
        print('Using archive config: ' + archive_configuration)
    except IOError:
        mssg = str().join([
            'Cannot read archive configuration file:  ', archive_configuration
        ])
        log_sig_exit('ERROR', mssg, sigevent_url)

    location = ""
    dom = xml.dom.minidom.parse(archive_config)
    archiveElements = dom.getElementsByTagName('Archive')
    for archiveElement in archiveElements:
        if str(archiveElement.attributes['id'].
               value).lower() == archive_root.lower():
            location = archiveElement.getElementsByTagName(
                'Location')[0].firstChild.data.strip()
            print "Archive location: " + location + " \n"
    if location == "":
        log_sig_err(
            'Archive "' + archive_root + '" not found in ' +
            archive_configuration, sigevent_url)
    return location


def get_tmslimits(tmsLimitId, tmslimits_configuration):
    """
    Gets TileMatrixSetLimits from a TileMatrixSetLimits configuration file based on the limit ID.
    Arguments:
        tmsLimitId -- the id of the TileMatrixSetLimit
        tmslimits_configuration -- the location of the TileMatrixSetLimits configuration file
    """
    try:
        # Open file.
        tmsLimits_config = open(tmslimits_configuration, 'r')
        print('Using TileMatrixSetLimits config: ' + tmslimits_configuration)
    except IOError:
        raise ValueError(str().join([
            'ERROR: Cannot read TileMatrixSetLimits configuration file:  ',
            tmslimits_configuration
        ]))

    tmsLimits = None
    dom = xml.dom.minidom.parse(tmsLimits_config)
    tmsLimitElements = dom.getElementsByTagName('TileMatrixSetLimits')

    for limitsElem in tmsLimitElements:
        if limitsElem.getAttribute('id') == tmsLimitId:
            tmsLimits = limitsElem
            break

    if not tmsLimits:
        raise ValueError('ERROR: TileMatrixSetLimits ID "' + tmsLimitId +
                         '" not found in ' + tmslimits_configuration)

    tmsLimits.removeAttribute('id')
    return tmsLimits


def get_projection(projectionId, projectionConfig, lcdir,
                   tilematrixset_configuration):
    """
    Gets projection metadata from a projection configuration file based on the projection ID.
    Arguments:
        projectionId -- the name of the projection and key used
        projectionConfig -- the location of the projection configuration file
    """
    try:
        # Open file.
        projection_config = open(projectionConfig, 'r')
        print('Using projection config: ' + projectionConfig + '\n')
    except IOError:
        mssg = str().join(
            ['Cannot read projection configuration file:  ', projectionConfig])
        log_sig_exit('ERROR', mssg, sigevent_url)

    dom = xml.dom.minidom.parse(projection_config)
    projection = None
    projectionTags = dom.getElementsByTagName('Projection')
    for projectionElement in projectionTags:
        if projectionElement.attributes['id'].value == projectionId:
            wkt = projectionElement.getElementsByTagName(
                'WKT')[0].firstChild.data.strip()
            try:
                wgsbbox = projectionElement.getElementsByTagName(
                    'WGS84BoundingBox')[0].toxml().replace(
                        "WGS84BoundingBox", "ows:WGS84BoundingBox")
            except:
                wgsbbox = ""
            try:
                boundbox = "\n         " + projectionElement.getElementsByTagName(
                    'BoundingBox')[0].toxml().replace("BoundingBox",
                                                      "ows:BoundingBox")
            except:
                boundbox = ""
            bbox = str(wgsbbox + boundbox).replace(
                "LowerCorner", "ows:LowerCorner").replace(
                    "UpperCorner", "ows:UpperCorner")
            # get corners...a bit messy
            lowercorner = xml.dom.minidom.parseString(
                "<bbox>" + str(boundbox + wgsbbox).replace("ows:", "") +
                "</bbox>").getElementsByTagName(
                    'LowerCorner')[0].firstChild.nodeValue.split(" ")
            uppercorner = xml.dom.minidom.parseString(
                "<bbox>" + str(boundbox + wgsbbox).replace("ows:", "") +
                "</bbox>").getElementsByTagName(
                    'UpperCorner')[0].firstChild.nodeValue.split(" ")
            tilematrixsets = {}
            try:
                # Open file.
                tilematrixsetconfig = open(tilematrixset_configuration, 'r')
                print('Using TileMatrixSet config: ' +
                      tilematrixset_configuration + '\n')
            except IOError:
                mssg = str().join([
                    'Cannot read TileMatrixSet configuration file:  ',
                    tilematrixset_configuration
                ])
                log_sig_exit('ERROR', mssg, sigevent_url)
            tms_dom = xml.dom.minidom.parse(tilematrixsetconfig)
            tms_projections = tms_dom.getElementsByTagName('Projection')
            tms_xml = ""
            for tms_projection in tms_projections:
                try:
                    if tms_projection.attributes['id'].value == projectionId:
                        tms_xml = '\n'.join(tms_projection.toxml().split(
                            '\n')[1:-1])  # remove <Projection> lines
                        tms_xml = re.sub(
                            r'<TileMatrixSet level="\d+">', '<TileMatrixSet>',
                            tms_xml)  # remove added level metadata
                        tileMatrixSetElements = tms_projection.getElementsByTagName(
                            'TileMatrixSet')
                        for tilematrixset in tileMatrixSetElements:
                            scale_denominators = tilematrixset.getElementsByTagName(
                                "ScaleDenominator")
                            if scale_denominators.length > 1:
                                scale = int(
                                    round(
                                        float(scale_denominators[0].firstChild.
                                              nodeValue.strip()) /
                                        float(scale_denominators[1].firstChild.
                                              nodeValue.strip())))
                            else:
                                scale = 2  # default to powers of 2 scale
                            print "TileMatrixSet: " + tilematrixset.getElementsByTagName(
                                'ows:Identifier'
                            )[0].firstChild.nodeValue.strip(
                            ) + " - levels: " + str(
                                tilematrixset.getElementsByTagName(
                                    "TileMatrix").
                                length) + ", overview scale: " + str(scale)
                            tilematrixsets[tilematrixset.getElementsByTagName(
                                'ows:Identifier')[0].firstChild.nodeValue.
                                           strip()] = TileMatrixSetMeta(
                                               tilematrixset.
                                               getElementsByTagName(
                                                   "TileMatrix").length, scale)

                except KeyError, e:
                    log_sig_exit(
                        'ERROR', 'Projection ' + projectionId + " " + str(e) +
                        ' missing in TileMatrixSet configuration ' +
                        tilematrixset_configuration, sigevent_url)

            projection = Projection(projectionId, wkt, bbox, tilematrixsets,
                                    tms_xml, lowercorner, uppercorner)

    if projection == None:
        mssg = "Projection " + projectionId + " could not be found in projection configuration file."
        raise Exception(mssg)

    return projection


def detect_time(time, archiveLocation, fileNamePrefix, year, has_zdb):
    """
    Checks time element to see if start or end time must be detected on the file system.
    Arguments:
        time -- the time element (DETECT) keyword is utilized
        archiveLocation -- the location of the archive data
        fileNamePrefix -- the prefix of the MRF files
        year -- whether or not the layer uses a year-based directory structure
        has_zdb -- whether or not the layer contains a zdb file
    """
    times = []
    print "\nAssessing time", time
    time = time.upper()
    detect = "DETECT"
    period = "P1D"
    period_value = 1  # numeric value of period
    archiveLocation = add_trailing_slash(archiveLocation)
    subdaily = False

    if not os.path.isdir(archiveLocation):
        message = archiveLocation + " is not a valid location"
        log_sig_err(message, sigevent_url)
        return times

    if (time == detect or time == ''
            or time.startswith(detect + '/P')) and has_zdb == False:
        #detect everything including breaks in date
        dates = []
        if year == True:
            filesearch = archiveLocation + '/[0-9]*/*[idx,shp,json]'
            if len(glob.glob(filesearch)
                   ) == 0:  # No files, maybe 'year' not specified correctly
                filesearch = archiveLocation + '/*[idx,shp,json]'
        else:
            filesearch = archiveLocation + '/*[idx,shp,json]'
        for f in glob.glob(filesearch):
            filename = os.path.basename(f)
            if str(filename).startswith(fileNamePrefix) and len(filename) == (
                    len(fileNamePrefix) + len("YYYYJJJ") + 5):
                try:
                    filetime = filename[-12:-5]
                    filedate = datetime.strptime(filetime, "%Y%j")
                    dates.append(filedate)
                except ValueError:
                    print "Skipping", filename
            elif str(filename).startswith(fileNamePrefix) and len(
                    filename) == (
                        len(fileNamePrefix) + len("YYYYJJJHHMMSS") + 5):
                try:
                    filetime = filename[-18:-5]
                    filedate = datetime.strptime(filetime, "%Y%j%H%M%S")
                    dates.append(filedate)
                    subdaily = True
                    period = "PT24H"
                except ValueError:
                    print "Skipping", filename
            else:
                print "Ignoring", filename
        dates = sorted(list(set(dates)))

        # DEBUG: Print the entire list of dates found for the product
        #for testdate in dates:
        #    print datetime.strftime(testdate,"%Y-%m-%dT%H:%M:%SZ")

        # Get period, attempt to figure out period (in days) if none
        if time.startswith(detect + '/P'):
            period = time.split('/')[1]
        else:
            if len(
                    dates
            ) > 3:  #check if the difference between first three dates are the same
                if subdaily == False:
                    diff1 = abs((dates[0] - dates[1]).days)
                    diff2 = abs((dates[1] - dates[2]).days)
                    diff3 = abs((dates[2] - dates[3]).days)
                    if diff1 == diff2 == diff3:
                        period = "P" + str(diff1) + "D"
                    elif 31 in [diff1, diff2, diff3]:
                        period = "P1M"
                    if 365 in [diff1, diff2, diff3]:
                        period = "P1Y"
                else:
                    diff1 = abs((dates[0] - dates[1]))
                    diff2 = abs((dates[1] - dates[2]))
                    diff3 = abs((dates[2] - dates[3]))
                    if diff1 == diff2 == diff3:
                        if diff1.seconds % 3600 == 0:
                            period = "PT" + str(diff1.seconds / 3600) + "H"
                        elif diff1.seconds % 60 == 0:
                            period = "PT" + str(diff1.seconds / 60) + "M"
                        else:
                            period = "PT" + str(diff1.seconds) + "S"
            message = "No period in time configuration for " + fileNamePrefix + " - detected " + period
            log_sig_warn(message, sigevent_url)
        print "Using period " + str(period)
        try:
            if subdaily == False:
                period_value = int(period[1:-1])
            else:
                period_value = int(period[2:-1])
        except ValueError:
            log_sig_err(
                "Mixed period values are not supported on server: " + period,
                sigevent_url)
        # Search for date ranges
        if len(dates) == 0:
            message = "No files with dates found for '" + fileNamePrefix + "' in '" + archiveLocation + "' - please check if data exists."
            log_sig_err(message, sigevent_url)
            startdate = datetime.now()  # default to now
        else:
            startdate = min(dates)
            print "Start of data " + datetime.strftime(startdate,
                                                       "%Y-%m-%dT%H:%M:%SZ")
        enddate = startdate  # set end date to start date for lone dates
        for i, d in enumerate(dates):
            # print d
            if period[-1] == "W":
                next_day = d + timedelta(weeks=period_value)
            elif period[-1] == "M" and subdaily == False:
                next_day = d + relativedelta(months=period_value)
            elif period[-1] == "Y":
                next_day = d + relativedelta(years=period_value)
            elif period[-1] == "H":
                next_day = d + relativedelta(hours=period_value)
            elif period[-1] == "M" and subdaily == True:
                next_day = d + relativedelta(minutes=period_value)
            elif period[-1] == "S":
                next_day = d + relativedelta(seconds=period_value)
            else:
                next_day = d + timedelta(days=period_value)

            try:
                if dates[i + 1] == next_day:
                    enddate = next_day  # set end date to next existing day
                else:  # end of range
                    if subdaily == False:
                        print "Break in data beginning on " + datetime.strftime(
                            next_day, "%Y-%m-%d")
                        start = datetime.strftime(startdate, "%Y-%m-%d")
                        end = datetime.strftime(enddate, "%Y-%m-%d")
                    else:
                        print "Break in data beginning on " + datetime.strftime(
                            next_day, "%Y-%m-%dT%H:%M:%SZ")
                        start = datetime.strftime(startdate,
                                                  "%Y-%m-%dT%H:%M:%SZ")
                        end = datetime.strftime(enddate, "%Y-%m-%dT%H:%M:%SZ")
                    times.append(start + '/' + end + '/' + period)
                    startdate = dates[i + 1]  # start new range loop
                    enddate = startdate
            except IndexError:
                # breaks when loop completes
                if subdaily == False:
                    start = datetime.strftime(startdate, "%Y-%m-%d")
                    end = datetime.strftime(enddate, "%Y-%m-%d")
                else:
                    start = datetime.strftime(startdate, "%Y-%m-%dT%H:%M:%SZ")
                    end = datetime.strftime(enddate, "%Y-%m-%dT%H:%M:%SZ")
                times.append(start + '/' + end + '/' + period)
                print "End of data " + end
                print "Time ranges: " + ", ".join(times)
                return times

    else:
        intervals = time.split('/')
        if intervals[0][0] == 'P':  #starts with period, so no start date
            start = detect
        else:
            start = ''
        has_period = False
        for interval in list(intervals):
            if len(interval) > 0:
                if interval[0] == 'P':
                    has_period = True
                    period = interval
                    intervals.remove(interval)
            else:
                intervals.remove(interval)
        if has_period == False:
            message = "No period in time configuration for " + fileNamePrefix
            if has_zdb == False:
                message = message + " - using P1D"
            log_sig_warn(message, sigevent_url)
        print "Using period " + period
        if len(intervals) == 2:
            start = intervals[0]
            end = intervals[1]
        else:
            if start == detect:
                end = intervals[0]
            else:
                start = intervals[0]
                end = detect

        if start == detect or end == detect:
            newest_year = ''
            oldest_year = ''
            if year == True:  # get newest and oldest years
                years = []
                for yearDirPath in glob.glob(archiveLocation + '/[0-9]*'):
                    if os.listdir(yearDirPath
                                  ) != []:  # check if directory is not empty
                        years.append(os.path.basename(yearDirPath))
                    else:
                        log_sig_warn(yearDirPath + " is empty", sigevent_url)
                    years.sort()
                if len(years) > 0:
                    oldest_year = years[0]
                    newest_year = years[-1]
                print "Year directories available: " + ",".join(years)
            if (newest_year == '' or oldest_year == '') and year == True:
                mssg = "No data files found in year directories in " + archiveLocation
                log_sig_warn(mssg, sigevent_url)
                return times
            elif year == True:
                print "Available range with data is %s to %s" % (oldest_year,
                                                                 newest_year)

        if start == detect:
            dates = []
            for f in glob.glob(archiveLocation + '/' + oldest_year +
                               '/*[idx,shp,json]'):
                filename = os.path.basename(f)
                if str(filename).startswith(fileNamePrefix) and len(
                        filename) == (
                            len(fileNamePrefix) + len("YYYYJJJ") + 5):
                    try:
                        filetime = filename[-12:-5]
                        filedate = datetime.strptime(filetime, "%Y%j")
                        dates.append(filedate)
                    except ValueError:
                        print "Skipping", filename
                elif str(filename).startswith(fileNamePrefix) and len(
                        filename) == (
                            len(fileNamePrefix) + len("YYYYJJJHHMMSS") + 5):
                    try:
                        filetime = filename[-18:-5]
                        filedate = datetime.strptime(filetime, "%Y%j%H%M%S")
                        dates.append(filedate)
                        subdaily = True
                    except ValueError:
                        print "Skipping", filename
                else:
                    print "Ignoring", filename
            if len(dates) == 0:
                message = "No valid files with dates found for '" + fileNamePrefix + "' in '" + archiveLocation + "/" + oldest_year + "' - please check if data exists."
                log_sig_err(message, sigevent_url)
                return times
            startdate = min(dates)
            if has_zdb == True:
                try:
                    zdb = archiveLocation + '/' + oldest_year + '/' + fileNamePrefix + datetime.strftime(
                        startdate, "%Y%j") + '_.zdb'
                    zkey = read_zkey(zdb, 'ASC')
                    startdate = datetime.strptime(str(zkey), "%Y%m%d%H%M%S")
                    subdaily = True
                except ValueError:
                    if zkey.lower() != "default":
                        log_sig_warn("No valid time found in " + zdb,
                                     sigevent_url)
            if subdaily == False:
                start = datetime.strftime(startdate, "%Y-%m-%d")
            else:
                start = datetime.strftime(startdate, "%Y-%m-%dT%H:%M:%SZ")

        if end == detect:
            dates = []
            for f in glob.glob(archiveLocation + '/' + newest_year +
                               '/*[idx,shp,json]'):
                filename = os.path.basename(f)
                if str(filename).startswith(fileNamePrefix) and len(
                        filename) == (
                            len(fileNamePrefix) + len("YYYYJJJ") + 5):
                    try:
                        filetime = filename[-12:-5]
                        filedate = datetime.strptime(filetime, "%Y%j")
                        dates.append(filedate)
                    except ValueError:
                        print "Skipping", filename
                elif str(filename).startswith(fileNamePrefix) and len(
                        filename) == (
                            len(fileNamePrefix) + len("YYYYJJJHHMMSS") + 5):
                    try:
                        filetime = filename[-18:-5]
                        filedate = datetime.strptime(filetime, "%Y%j%H%M%S")
                        dates.append(filedate)
                        subdaily = True
                    except ValueError:
                        print "Skipping", filename
                else:
                    print "Ignoring", filename
            enddate = max(dates)
            if has_zdb == True:
                try:
                    zdb = archiveLocation + '/' + newest_year + '/' + fileNamePrefix + datetime.strftime(
                        enddate, "%Y%j") + '_.zdb'
                    zkey = read_zkey(zdb, 'DESC')
                    enddate = datetime.strptime(str(zkey), "%Y%m%d%H%M%S")
                    subdaily = True
                except ValueError:
                    if zkey.lower() != "encoded":
                        log_sig_warn("No valid time found in " + zdb,
                                     sigevent_url)
            if subdaily == False:
                end = datetime.strftime(enddate, "%Y-%m-%d")
            else:
                end = datetime.strftime(enddate, "%Y-%m-%dT%H:%M:%SZ")

        if has_zdb == True and has_period == False:
            time = start + '/' + end
        else:
            time = start + '/' + end + '/' + period
        print str(time)
        times.append(time)

    return times


def read_zkey(zdb, sort):
    """
    Reads z-index database file and returns the first or last key depending on sort order
    Arguments:
        zdb -- the z-index database file name
        sort -- the sort order
    """
    try:
        log_info_mssg("Connecting to " + zdb)
        db_exists = os.path.isfile(zdb)
        if db_exists == False:
            log_sig_err(zdb + " does not exist", sigevent_url)
            return "Error"
        else:
            con = sqlite3.connect(zdb, timeout=60)  # 1 minute timeout
            cur = con.cursor()

            # Check for existing key
            cur.execute("SELECT key_str FROM ZINDEX ORDER BY key_str " + sort +
                        " LIMIT 1;")
            try:
                key = cur.fetchone()[0].split("|")[0]
                log_info_mssg("Retrieved key " + key)
            except:
                return "Error"
            if con:
                con.close()
            return key

    except sqlite3.Error, e:
        if con:
            con.rollback()
        mssg = "%s:" % e.args[0]
        log_sig_err(mssg, sigevent_url)


def get_file_from_time(timestr, fileNamePrefix, include_year_dir, has_zdb):
    """
    Retrieves the filename (without extension) of a file based on a time string and file name prefix
    Arguments:
        timestr -- time string (%Y-%m-%d or %Y-%m-%dT%H:%M:%SZ)
        fileNamePrefix -- the prefix of the MRF files
        include_year_dir -- whether or not to include the parent year directory
        has_zdb -- whether or not the layer contains a zdb file
    """
    if 'T' in timestr:  # sub-daily files
        t = datetime.strptime(timestr, "%Y-%m-%dT%H:%M:%SZ")
        if has_zdb:
            filename = fileNamePrefix + datetime.strftime(t, "%Y%j") + "_"
        else:
            filename = fileNamePrefix + datetime.strftime(t,
                                                          "%Y%j%H%M%S") + "_"
        last_year = datetime.strftime(t, "%Y")
    else:
        t = datetime.strptime(timestr, "%Y-%m-%d")
        filename = fileNamePrefix + datetime.strftime(t, "%Y%j") + "_"
        last_year = datetime.strftime(t, "%Y")
    if include_year_dir:
        return str(last_year) + "/" + filename
    else:
        return filename


def generate_legend(colormap, output, legend_url, format, orientation):
    """
    Generate a legend graphic from GIBS color map. 
    Returns: WMTS <LegendURL> metadata tag, legend width, legend height.
    Arguments:
        colormap -- the color map file name
        output -- the output file name
        legend_url -- URL to access legend from GetCapabilities
        format -- the format of the legend ('png' or 'svg')
        orientation -- the orientation of the legend
    """

    print "\nLegend location: " + output
    print "Legend URL: " + legend_url
    print "Color Map: " + colormap
    print "Format: " + format
    print "Orientation: " + orientation
    pt = 1.25  #pixels in point

    legend_url_metadata = ''
    width  = ''
    height = ''

    if format not in ["svg","png"]:
        log_sig_err("Error generating legend; Invalid format: " + format, sigevent_url)
        return
    elif orientation not in ["horizontal","vertical"]:
        log_sig_err("Error generating legend; Invalid orientation: " + orientation, sigevent_url)
        return

    cmd = 'oe_generate_legend.py -c ' + colormap + ' -o ' + output + ' -r ' + orientation + ' -f ' + format

    if os.path.isfile(output) == False:
        print "Generating new legend"

        try:
            run_command(cmd, sigevent_url)
        except Exception, e:
            log_sig_err("Error generating legend: " + str(e), sigevent_url)
    else:
        print "Legend already exists"
        try:
            colormap_file = urllib.urlopen(colormap)
            last_modified = colormap_file.info().getheader("Last-Modified")
            colormap_file.close()
            colormap_time = datetime.strptime(last_modified,
                                              "%a, %d %b %Y %H:%M:%S GMT")
            legend_time = datetime.fromtimestamp(os.path.getmtime(output))
            print "Color map last modified on: " + str(colormap_time)
            print "Legend last modified on: " + str(legend_time)
            if colormap_time > legend_time:
                print "Updated color map found, generating new legend"
                run_command(cmd, sigevent_url)
            else:
                print "Updated color map not found, skipping legend generation"
        except Exception, e:
            log_sig_err("Error generating legend: " + str(e), sigevent_url)

    # check file
    try:
        if format == "svg":
            # Open file.
            svg = open(output, 'r')

            # get width and height
            dom = xml.dom.minidom.parse(svg)
            svgElement = dom.getElementsByTagName('svg')[0]
            height = float(svgElement.attributes['height'].value.replace('pt',
                                                                         '')) * pt
            width = float(svgElement.attributes['width'].value.replace('pt', '')) * pt
            svg.close()

            if orientation == 'horizontal':
                legend_url_metadata = '<LegendURL format="image/svg+xml" xlink:type="simple" xlink:role="http://earthdata.nasa.gov/gibs/legend-type/horizontal" xlink:href="%s" xlink:title="GIBS Color Map Legend: Horizontal" width="%d" height="%d"/>' % (
                    legend_url, int(width), int(height))
            else:
                legend_url_metadata = '<LegendURL format="image/svg+xml" xlink:type="simple" xlink:role="http://earthdata.nasa.gov/gibs/legend-type/vertical" xlink:href="%s" xlink:title="GIBS Color Map Legend: Vertical" width="%d" height="%d"/>' % (
                    legend_url, int(width), int(height))


        # png
        else:
            # get width and height
            gdalinfo_command_list = ['gdalinfo', '-json', output]
            gdalinfo = subprocess.Popen(gdalinfo_command_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            outputInfo = json.loads(gdalinfo.stdout.read())

            width  = outputInfo["size"][0]
            height = outputInfo["size"][1]

            if orientation == 'horizontal':
                legend_url_metadata = '<LegendURL format="image/png" xlink:type="simple" xlink:role="http://earthdata.nasa.gov/gibs/legend-type/horizontal" xlink:href="%s" xlink:title="GIBS Color Map Legend: Horizontal" width="%d" height="%d"/>' % (
                    legend_url, int(width), int(height))
            else:
                legend_url_metadata = '<LegendURL format="image/png" xlink:type="simple" xlink:role="http://earthdata.nasa.gov/gibs/legend-type/vertical" xlink:href="%s" xlink:title="GIBS Color Map Legend: Vertical" width="%d" height="%d"/>' % (
                legend_url, int(width), int(height))

    except IOError:
        mssg = str().join(['Cannot read legend file:  ', output])
        log_sig_err(mssg, sigevent_url)


    return legend_url_metadata, width, height


def generate_empty_tile(colormap, output, width, height):
    """
    Generate an empty tile from nodata value in GIBS color map.
    Arguments:
        colormap -- the color map file name
        output -- the output file name
        width -- the width of the empty tile
        height -- the height of the empty tile
    """

    print "Generating empty tile"
    print "Empty Tile Location: " + output
    print "Color Map: " + colormap
    print "Width: " + str(width)
    print "Height: " + str(height)

    empty_size = 0

    try:
        cmd = 'oe_generate_empty_tile.py -c ' + colormap + ' -o ' + output + ' -x ' + str(
            width) + ' -y ' + str(height)
        run_command(cmd, sigevent_url)
    except Exception, e:
        log_sig_err("Error generating empty tile: " + str(e), sigevent_url)

    # check file
    try:
        # Get file size
        empty_size = os.path.getsize(output)
        print "Empty tile size: " + str(empty_size)
    except:
        mssg = str().join(['Cannot read generated empty tile:  ', output])
        log_sig_err(mssg, sigevent_url)

    return empty_size


def generate_links(detected_times, archiveLocation, fileNamePrefix, year,
                   dataFileLocation, has_zdb):
    """
    Generate a archive links for a layer based on the last provided time period
    Arguments:
        detected_times -- the list of available time periods
        archiveLocation -- the location of the archive data
        fileNamePrefix -- the prefix of the MRF files
        year -- whether or not the layer uses a year-based directory structure
        dataFileLocation -- file location for the default data file
        has_zdb -- whether or not the layer contains a zdb file
    """
    last_time = detected_times[-1].split("/")[1]
    if os.path.isfile(
            archiveLocation +
            get_file_from_time(last_time, fileNamePrefix, year, has_zdb) +
            ".idx"
    ) == False:  # Detect the last time if file for specified time cannot be found
        log_sig_warn(
            "Files for specified last time of " + last_time +
            " cannot be found for " + fileNamePrefix +
            ", attempting to detect instead", sigevent_url)
        if len(detected_times[-1].split("/")) == 3:
            period = "/" + detected_times[-1].split("/")[2]
        else:
            period = ""
        try:
            last_time = detect_time(
                detected_times[-1].split("/")[0] + "/DETECT" + period,
                archiveLocation, fileNamePrefix, year,
                has_zdb)[-1].split("/")[1]
        except IndexError:
            log_sig_err(
                "Unable to generate links due to no data files found for " +
                fileNamePrefix, sigevent_url)
            return ""
    print "Current layer time for soft links: " + last_time

    link_pre, data_ext = os.path.splitext(dataFileLocation)
    link_dir = os.path.dirname(link_pre)
    filename = get_file_from_time(last_time, fileNamePrefix, year, has_zdb)
    mrf = archiveLocation + filename + ".mrf"
    idx = archiveLocation + filename + ".idx"
    data = archiveLocation + filename + data_ext
    zdb = archiveLocation + filename + ".zdb"
    mrf_link = link_pre + ".mrf"
    idx_link = link_pre + ".idx"
    data_link = link_pre + data_ext
    zdb_link = link_pre + ".zdb"

    # make sure link directory exists
    if not os.path.exists(link_dir):
        os.makedirs(link_dir)
        print "Created directory " + link_dir

    if os.path.isfile(mrf):
        if os.path.lexists(mrf_link):
            os.remove(mrf_link)
            print "Removed existing file " + mrf_link
        os.symlink(mrf, mrf_link)
        print "Created soft link " + mrf_link + " -> " + mrf
    if os.path.isfile(idx):
        if os.path.lexists(idx_link):
            os.remove(idx_link)
            print "Removed existing file " + idx_link
        os.symlink(idx, idx_link)
        print "Created soft link " + idx_link + " -> " + idx
    else:
        if data_ext != ".shp" or data_ext != ".json":
            log_sig_warn("Default MRF index file " + idx + " does not exist",
                         sigevent_url)
    if os.path.isfile(data):
        if os.path.lexists(data_link):
            os.remove(data_link)
            print "Removed existing file " + data_link
        os.symlink(data, data_link)
        print "Created soft link " + data_link + " -> " + data
    else:
        log_sig_warn("Default MRF data file " + data + " does not exist",
                     sigevent_url)
    if os.path.isfile(zdb):
        if os.path.lexists(zdb_link):
            os.remove(zdb_link)
            print "Removed existing file " + zdb_link
        os.symlink(zdb, zdb_link)
        print "Created soft link " + zdb_link + " -> " + zdb

    # special handling for shapefiles
    if data_ext == ".shp":
        files = glob.glob(archiveLocation + filename + "*")
        for sfile in files:
            ext = os.path.splitext(os.path.basename(sfile))[1]
            if os.path.lexists(link_pre + ext):
                os.remove(link_pre + ext)
                print "Removed existing file " + link_pre + ext
            os.symlink(sfile, link_pre + ext)
            print "Created soft link " + link_pre + ext + " -> " + sfile

    return mrf_link, idx_link, data_link, zdb_link


#-------------------------------------------------------------------------------

print 'OnEarth Layer Configurator v' + versionNumber

if os.environ.has_key('LCDIR') == False:
    print 'LCDIR environment variable not set.\nLCDIR should point to your OnEarth layer_config directory.\n'
    lcdir = os.path.abspath(os.path.dirname(__file__) + '/..')
else:
    lcdir = os.environ['LCDIR']

usageText = 'oe_configure_layer.py --conf_file [layer_configuration_file.xml] --layer_dir [$LCDIR/layers/] --lcdir [$LCDIR] --projection_config [projection.xml] --time [ISO 8601] --restart_apache --no_xml --no_cache --no_twms --no_wmts --generate_legend --generate_links --skip_empty_tiles --create_mapfile'

# Define command line options and args.
parser = OptionParser(usage=usageText, version=versionNumber)
parser.add_option(
    '-a',
    '--archive_config',
    action='store',
    type='string',
    dest='archive_configuration',
    help=
    'Full path of archive configuration file.  Default: $LCDIR/conf/archive.xml'
)
parser.add_option(
    '-c',
    '--conf_file',
    action='store',
    type='string',
    dest='layer_config_filename',
    help='Full path of layer configuration filename.')
parser.add_option(
    '-d',
    '--layer_dir',
    action='store',
    type='string',
    dest='layer_directory',
    help=
    'Full path of directory containing configuration files for layers.  Default: $LCDIR/layers/'
)
parser.add_option(
    "-e",
    "--skip_empty_tiles",
    action="store_true",
    dest="skip_empty_tiles",
    default=False,
    help=
    "Do not generate empty tiles for layers using color maps in configuration."
)
parser.add_option(
    "-g",
    "--generate_legend",
    action="store_true",
    dest="generate_legend",
    default=False,
    help="Generate legends for layers using color maps in configuration.")
parser.add_option(
    '-l',
    '--lcdir',
    action='store',
    type='string',
    dest='lcdir',
    default=lcdir,
    help=
    'Full path of the OnEarth Layer Configurator (layer_config) directory.  Default: $LCDIR'
)
parser.add_option(
    '-m',
    '--tilematrixset_config',
    action='store',
    type='string',
    dest='tilematrixset_configuration',
    help=
    'Full path of TileMatrixSet configuration file.  Default: $LCDIR/conf/tilematrixsets.xml'
)
parser.add_option(
    "-n",
    "--no_twms",
    action="store_true",
    dest="no_twms",
    default=False,
    help="Do not use configurations for Tiled-WMS")
parser.add_option(
    '-p',
    '--projection_config',
    action='store',
    type='string',
    dest='projection_configuration',
    help=
    'Full path of projection configuration file.  Default: $LCDIR/conf/projection.xml'
)
parser.add_option(
    "-r",
    "--restart_apache",
    action="store_true",
    dest="restart",
    default=False,
    help="Restart the Apache server on completion (requires sudo).")
parser.add_option(
    "-s",
    "--send_email",
    action="store_true",
    dest="send_email",
    default=False,
    help="Send email notification for errors and warnings.")
parser.add_option(
    '--email_server',
    action='store',
    type='string',
    dest='email_server',
    default='',
    help=
    'The server where email is sent from (overrides configuration file value)')
parser.add_option(
    '--email_recipient',
    action='store',
    type='string',
    dest='email_recipient',
    default='',
    help=
    'The recipient address for email notifications (overrides configuration file value)'
)
parser.add_option(
    '--email_sender',
    action='store',
    type='string',
    dest='email_sender',
    default='',
    help=
    'The sender for email notifications (overrides configuration file value)')
parser.add_option(
    '--email_logging_level',
    action='store',
    type='string',
    dest='email_logging_level',
    default='ERROR',
    help=
    'Logging level for email notifications: ERROR, WARN, or INFO.  Default: ERROR'
)
parser.add_option(
    '-t',
    '--time',
    action='store',
    type='string',
    dest='time',
    help=
    'ISO 8601 time(s) for single configuration file (conf_file must be specified).'
)
parser.add_option(
    "-w",
    "--no_wmts",
    action="store_true",
    dest="no_wmts",
    default=False,
    help="Do not use configurations for WMTS.")
parser.add_option(
    "-x",
    "--no_xml",
    action="store_true",
    dest="no_xml",
    default=False,
    help="Do not generate getCapabilities and getTileService XML.")
parser.add_option(
    "-y",
    "--generate_links",
    action="store_true",
    dest="generate_links",
    default=False,
    help=
    "Generate default/current day links in the archive for time varying layers."
)
parser.add_option(
    "-z",
    "--no_cache",
    action="store_true",
    dest="no_cache",
    default=False,
    help=
    "Do not copy cache configuration files and Apache configs to final location."
)

parser.add_option(
    '--tmslimits_config',
    action='store',
    type='string',
    dest='tmslimits_configuration',
    help=
    'Full path of TileMatrixSet configuration file.  Default: $LCDIR/conf/tilematrixsetlimits.xml'
)

parser.add_option(
    "--create_mapfile",
    action="store_true",
    dest="create_mapfile",
    default=False,
    help="Create MapServer configuration.")

# Read command line args.
(options, args) = parser.parse_args()
# Configuration filename.
configuration_filename = options.layer_config_filename
# Command line set LCDIR.
lcdir = options.lcdir
# Configuration directory.
if options.layer_directory:
    configuration_directory = options.layer_directory
else:
    configuration_directory = lcdir + '/layers/'
# No XML configurations (getCapabilities, getTileService)
no_xml = options.no_xml
# No cache configuration.
no_cache = options.no_cache
# No Tiled-WMS configuration.
no_twms = options.no_twms
# No WMTS configuration.
no_wmts = options.no_wmts
# No MapServer configuration.
create_mapfile = options.create_mapfile
# Do restart Apache.
restart = options.restart
# Time for conf file.
configuration_time = options.time
# Generate Empty Tiles
skip_empty_tiles = options.skip_empty_tiles
# Generate legends
legend = options.generate_legend
# Generate links
links = options.generate_links
# Projection configuration
if options.projection_configuration:
    projection_configuration = options.projection_configuration
else:
    projection_configuration = lcdir + '/conf/projection.xml'
# TileMatrixSet configuration
if options.tilematrixset_configuration:
    tilematrixset_configuration = options.tilematrixset_configuration
else:
    tilematrixset_configuration = lcdir + '/conf/tilematrixsets.xml'
# Archive configuration
if options.archive_configuration:
    archive_configuration = options.archive_configuration
else:
    archive_configuration = lcdir + '/conf/archive.xml'

# TileMatrixSetLimits configuration
if options.tmslimits_configuration:
    tmslimits_configuration = options.tmslimits_configuration
else:
    tmslimits_configuration = lcdir + '/conf/tilematrixsetlimits.xml'

# Send email.
send_email = options.send_email
# Email server.
email_server = options.email_server
# Email recipient
email_recipient = options.email_recipient
# Email recipient
email_sender = options.email_sender
# Email logging level
logging_level = options.email_logging_level.upper()
# Email metadata replaces sigevent_url
if send_email:
    sigevent_url = (email_server, email_recipient, email_sender, logging_level)
else:
    sigevent_url = ''

print 'Using ' + lcdir + ' as $LCDIR.'

if no_xml:
    log_info_mssg(
        "no_xml specified, getCapabilities and getTileService files will be staged only"
    )
if no_cache:
    log_info_mssg(
        "no_cache specified, cache configuration files will be staged only")
    restart = False
if not create_mapfile:
    log_info_mssg("create_mapfile not specified, no mapfiles will be created")
if no_twms and no_wmts and not create_mapfile:
    log_info_mssg(
        "no_twms and no_wmts and create_mapfile not specified, nothing to do...exiting"
    )
    exit()

if configuration_time:
    if configuration_filename == None:
        print "A configuration file must be specified with --time"
        exit()
    else:
        print "Using time='" + configuration_time + "' for " + configuration_filename

# set location of tools
if os.path.isfile(os.path.abspath(lcdir) + '/bin/oe_create_cache_config'):
    depth = os.path.abspath(lcdir) + '/bin'
elif distutils.spawn.find_executable('oe_create_cache_config') != None:
    depth = distutils.spawn.find_executable('oe_create_cache_config').split(
        '/oe_create_cache_config')[0]
else:
    depth = '/usr/bin'  # default

# Read XML configuration files.

conf_files = []
wmts_endpoints = {}
twms_endpoints = {}
wms_endpoints = {}

if not options.layer_config_filename:
    conf = subprocess.Popen(
        'ls ' + configuration_directory + '/*.xml',
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE).stdout
    for line in conf:
        conf_files.append(line.strip())
else:
    # use only the solo MRF when specified
    conf_files.append(configuration_filename)

print 'Configuration file(s):'
print conf_files
if conf_files == []:
    mssg = 'No configuration files found.'
    log_sig_exit('ERROR', mssg, sigevent_url)

for conf in conf_files:
    current_conf = conf
    try:
        # Open file.
        config_file = open(conf, 'r')
        print('\nUsing config: ' + conf)
    except IOError:
        log_sig_err(str().join(['Cannot read configuration file: ', conf]),
                    sigevent_url)
        continue
    else:
        dom = xml.dom.minidom.parse(config_file)

        # Get environment
        try:
            environmentConfig = get_dom_tag_value(dom, 'EnvironmentConfig')
            try:
                environment = get_environment(environmentConfig, sigevent_url)
            except Exception, e:
                log_sig_err(str(e), sigevent_url)
                continue
        except IndexError:
            log_sig_err(
                'Required <EnvironmentConfig> element is missing in ' + conf,
                sigevent_url)
            continue

        # Get default email server and recipient if not override
        if options.email_server == '':
            email_server = environment.emailServer
        if options.email_recipient == '':
            email_recipient = environment.emailRecipient
        if options.email_sender == '':
            email_sender = environment.emailSender
        if send_email:
            sigevent_url = (email_server, email_recipient, email_sender,
                            logging_level)
            if email_recipient == '':
                log_sig_err("No email recipient provided for notifications.",
                            sigevent_url)

        wmts_getCapabilities = environment.getCapabilities_wmts
        twms_getCapabilities = environment.getCapabilities_twms
        getTileService = environment.getTileService

        # Reprojected layers are handled by a separate script
        if dom.getElementsByTagName('ReprojectLayerConfig'):
            projection = get_projection('EPSG:3857', projection_configuration,
                                        lcdir, tilematrixset_configuration)
            print 'Configuring reprojection layers...'
            base_twms_gc = lcdir + '/conf/getcapabilities_base_twms.xml'
            base_twms_get_tile_service = lcdir + '/conf/gettileservice_base.xml'
            base_wmts_gc = lcdir + '/conf/getcapabilities_base_wmts.xml'
            reproject_warnings, reproject_errors = build_reproject_configs(
                conf,
                tilematrixset_configuration,
                wmts=not no_wmts,
                twms=not no_twms,
                create_gc=not no_xml,
                sigevent_url=sigevent_url,
                stage_only=no_cache,
                base_wmts_gc=base_wmts_gc,
                base_twms_gc=base_twms_gc,
                base_twms_get_tile_service=base_twms_get_tile_service,
                create_mapfile=create_mapfile)
            warnings += reproject_warnings
            errors += reproject_errors
            wmtsEndPoint = environment.wmts_dir
            twmsEndPoint = environment.twms_dir
            cacheLocation_wmts = environment.cacheLocation_wmts
            cacheBasename_wmts = environment.cacheBasename_wmts
            cacheLocation_twms = environment.cacheLocation_twms
            cacheBasename_twms = environment.cacheBasename_twms
            wmts_endpoints[wmtsEndPoint] = WMTSEndPoint(
                wmtsEndPoint, cacheLocation_wmts, cacheBasename_wmts,
                wmts_getCapabilities, projection)
            twms_endpoints[twmsEndPoint] = TWMSEndPoint(
                twmsEndPoint, cacheLocation_twms, cacheBasename_twms,
                twms_getCapabilities, getTileService, projection)
            wms_endpoints[environment.mapfileStagingLocation] = WMSEndPoint(
                environment.mapfileStagingLocation,
                environment.mapfileLocation,
                environment.mapfileLocationBasename,
                environment.mapfileConfigLocation,
                environment.mapfileConfigBasename)
            continue
        
        # Stage layers XML from remote GetCapabilities if config found
        if dom.getElementsByTagName('RemoteGetCapabilities'):
            remote_warnings, remote_errors = get_remote_layers(conf,
                                                               wmts=not no_wmts,
                                                               twms=not no_twms,
                                                               sigevent_url=sigevent_url,
                                                               create_mapfile=create_mapfile)
            warnings += remote_warnings
            errors += remote_errors
            continue

        #Vector parameters
        try:
            vectorType = dom.getElementsByTagName('VectorType')[0].firstChild.nodeValue
            try:
                mapfileLayerContents = dom.getElementsByTagName(
                    'MapfileLayerContents')[0].firstChild.nodeValue
            except IndexError:
                mapfileLayerContents = None
        except IndexError:
            vectorType = None
            mapfileLayerContents = None

        #Required parameters
        try:
            identifier = get_dom_tag_value(dom, 'Identifier')
        except IndexError:
            log_sig_err('Required <Identifier> element is missing in ' + conf,
                        sigevent_url)
            continue
        try:
            title = get_dom_tag_value(dom, 'Title')
        except IndexError:
            log_sig_err('Required <Title> element is missing in ' + conf,
                        sigevent_url)
            continue
        try:
            is_encoded = False
            compression = get_dom_tag_value(dom, 'Compression')
            compression = compression.upper()
            if compression == "JPG":
                compression = "JPEG"
            if compression == "PPNG":
                compression = "PNG"
            if compression == "TIFF":
                compression = "TIF"
            if compression == "EPNG":
                compression = "PNG"
                is_encoded = True
            if compression not in [
                    "JPEG", "PNG", "EPNG", "TIF", "LERC", "MVT"
            ]:
                log_sig_err(
                    '<Compression> must be either JPEG, PNG, TIF, LERC, or MVT in '
                    + conf, sigevent_url)
                continue
        except IndexError:
            if vectorType is None:
                log_sig_err(
                    'Required <Compression> element is missing in ' + conf,
                    sigevent_url)
                continue
            else:
                compression = "None"
        try:
            tilematrixset = get_dom_tag_value(dom, 'TileMatrixSet')
        except:
            if vectorType is None:
                log_sig_err(
                    'Required <TileMatrixSet> element is missing in ' + conf,
                    sigevent_url)
                continue
            else:
                tilematrixset = "None"
        try:
            emptyTileSize = int(get_dom_tag_value(dom, 'EmptyTileSize'))
        except IndexError:
            try:
                emptyTileSize = ""
                emptyTile = get_dom_tag_value(dom, 'EmptyTile')
            except IndexError:  # Required if EmptyTile is not specified
                if vectorType is None:
                    log_sig_err(
                        'Required <EmptyTileSize> or <EmptyTile> element is missing in '
                        + conf, sigevent_url)
                    continue
        try:
            fileNamePrefix = get_dom_tag_value(dom, 'FileNamePrefix')
        except IndexError:
            log_sig_err(
                'Required <FileNamePrefix> element is missing in ' + conf,
                sigevent_url)
            continue
        try:
            environmentConfig = get_dom_tag_value(dom, 'EnvironmentConfig')
            try:
                environment = get_environment(environmentConfig, sigevent_url)
            except Exception, e:
                log_sig_err(str(e), sigevent_url)
                continue
        except IndexError:
            log_sig_err(
                'Required <EnvironmentConfig> element is missing in ' + conf,
                sigevent_url)
            continue

        cacheLocation_wmts = environment.cacheLocation_wmts
        cacheBasename_wmts = environment.cacheBasename_wmts
        cacheLocation_twms = environment.cacheLocation_twms
        cacheBasename_twms = environment.cacheBasename_twms
        cacheConfig = cacheLocation_wmts  # default to WMTS cache location
        wmtsServiceUrl = environment.wmtsServiceUrl
        twmsServiceUrl = environment.twmsServiceUrl

        # Optional parameters
        try:
            tiledGroupName = get_dom_tag_value(dom, 'TiledGroupName')
        except:
            tiledGroupName = identifier.replace("_", " ") + " tileset"
        try:
            wmsSourceLoc = get_dom_tag_value(dom, 'WMSSourceLoc')
        except:
            wmsSourceLoc = "Local"
        try:
            wmsGroupName = get_dom_tag_value(dom, 'WMSGroupName')
        except:
            wmsGroupName = None
        try:
            wmsLayerGroupName = get_dom_tag_value(dom, 'WMSLayerGroupName')
        except:
            wmsLayerGroupName = None
        try:
            abstract = get_dom_tag_value(dom, 'Abstract')
        except:
            abstract = identifier + " abstract"
        try:
            archiveLocation = get_dom_tag_value(dom, 'ArchiveLocation')
        except:
            archiveLocation = None
        try:
            static = dom.getElementsByTagName(
                'ArchiveLocation')[0].attributes['static'].value.lower() in [
                    'true'
                ]
        except:
            static = True
        try:
            year = dom.getElementsByTagName(
                'ArchiveLocation')[0].attributes['year'].value.lower() in [
                    'true'
                ]
        except:
            year = False
        try:
            subdaily = dom.getElementsByTagName(
                'ArchiveLocation')[0].attributes['subdaily'].value.lower() in [
                    'true'
                ]
        except:
            subdaily = False
        try:
            archive_root = get_archive(
                dom.getElementsByTagName('ArchiveLocation')[0].
                attributes['root'].value, archive_configuration)
        except:
            archive_root = ""
        archiveLocation = archive_root + archiveLocation

        tmsLimits = None
        try:
            tmsLimitId = get_dom_tag_value(dom, 'TileMatrixSetLimitsId')
            tmsLimits = get_tmslimits(tmsLimitId, tmslimits_configuration)
        except IndexError:
            pass
        except ValueError as e:
            errors.append(e)

        try:
            headerFileName = get_dom_tag_value(dom, 'HeaderFileName')
        except:
            headerFileName = None
        try:
            dataFileLocation = get_dom_tag_value(dom, 'DataFileLocation')
        except:
            dataFileLocation = None
        try:
            indexFileLocation = get_dom_tag_value(dom, 'IndexFileLocation')
        except:
            indexFileLocation = None
        try:
            zIndexFileLocation = get_dom_tag_value(dom, 'ZIndexFileLocation')
        except:
            zIndexFileLocation = None
        try:
            projection = get_projection(
                get_dom_tag_value(dom, 'Projection'), projection_configuration,
                lcdir, tilematrixset_configuration)
        except IndexError:
            log_sig_err('Required <Projection> element is missing in ' + conf,
                        sigevent_url)
            continue
        except Exception, e:
            log_sig_err(str(e), sigevent_url)
            continue

        # Modified in 0.9 to allow for multiple versioned colormaps

        # Sort out any empty ColorMap tags
        colormaps = []
        for colormap in dom.getElementsByTagName('ColorMap'):
            if colormap.firstChild:
                colormaps.append(colormap)

        # Set default colormap (if none indicated, picks the last colormap found)
        default_colormap = None
        if colormaps:
            if len(colormaps) == 1:
                default_colormap = colormaps[0]
            else:
                for colormap in colormaps:
                    if 'default' in colormap.attributes.keys(
                    ) and colormap.attributes['default'].value == 'true':
                        if default_colormap is not None:
                            err_msg = 'Multiple <ColorMap> elements have "default=true" attribute but only one is allowed, using ' + colormap.toxml(
                            )
                            log_sig_err(err_msg, sigevent_url)
                        default_colormap = colormap
            if len(colormaps) > 1 and default_colormap is None:
                default_colormap = colormaps[-1]
                err_msg = 'Multiple <ColorMap> elements but none have "default=true" attribute, using ' + default_colormap.toxml(
                )
                log_sig_err(err_msg, sigevent_url)

        # Match <ColorMapLocation> and <ColorMapURL> to colormaps with the same version and set them as attributes of the <ColorMap>
        if colormaps:
            for colormap in colormaps:
                if 'version' not in colormap.attributes.keys():
                    colormap.attributes['version'] = ''

                colormap_value = colormap.firstChild.nodeValue
                version = colormap.attributes['version'].value
                location = next(
                    (location.firstChild.nodeValue
                     for location in environment.colormap_dirs
                     if location.attributes['version'].value == version), None)
                url = next((url.firstChild.nodeValue
                            for url in environment.colormapUrls
                            if url.attributes['version'].value == version),
                           None)

                if not location:
                    location = ''
                    err_msg = "ColorMapLocation for version '{0}' not defined for environment {1} - Trying colormap path {2}".format(
                        version, environmentConfig, colormap_value)
                    log_sig_warn(err_msg, sigevent_url)

                if not url:
                    url = ''
                    err_msg = "ColorMapURL for version '{0}' not defined for environment {1} - Trying colormap path {2}".format(
                        version, environmentConfig, colormap_value)
                    log_sig_warn(err_msg, sigevent_url)

                colormap.attributes['url'] = url
                colormap.attributes['location'] = location

        # Similar treatment as ColorMap for VectorStyleJSON
        # Supporting "legacy" tag name for now
        stylejson_elems = dom.getElementsByTagName('StyleJSON')
        stylejson_elems.extend(dom.getElementsByTagName('VectorStyleJSON'))
        stylejsons = []
        for stylejson in stylejson_elems:
            if stylejson.firstChild:
                stylejsons.append(stylejson)

        # Set default StyleJSON
        default_stylejson = None
        if stylejsons:
            if len(stylejsons) == 1:
                default_stylejson = stylejsons[0]
            else:
                for stylejson in stylejsons:
                    if 'default' in stylejson.attributes.keys() and stylejson.attributes['default'].value == 'true':
                        if default_stylejson is not None:
                            err_msg = 'Multiple <VectorStyleJSON> elements have "default=true" attribute but only one is allowed, using ' + stylejson.toxml()
                            log_sig_err(err_msg, sigevent_url)
                        default_stylejson = stylejson
            if len(stylejsons) > 1 and default_stylejson is None:
                default_stylejson = stylejsons[-1]
                err_msg = 'Multiple <VectorStyleJSON> elements but none have "default=true" attribute, using ' + default_stylejson.toxml()
                log_sig_err(err_msg, sigevent_url)

        # Match <StyleJSONLocation> and <StyleJSONURL> to style json files with the same version and set them as attributes of the <VectorStyleJSON>
        if stylejsons:
            for stylejson in stylejsons:
                if 'version' not in stylejson.attributes.keys():
                    stylejson.attributes['version'] = ''

                stylejson_value = stylejson.firstChild.nodeValue
                version = stylejson.attributes['version'].value
                location = next(
                    (location.firstChild.nodeValue
                     for location in environment.stylejson_dirs
                     if location.attributes['version'].value == version), None)
                url = next((url.firstChild.nodeValue
                            for url in environment.stylejsonUrls
                            if url.attributes['version'].value == version),
                           None)

                if not location:
                    location = ''
                    err_msg = "StyleJSONLocation for version '{0}' not defined for environment {1} - Trying VectorStyleJSON path {2}".format(
                        version, environmentConfig, stylejson_value)
                    log_sig_warn(err_msg, sigevent_url)

                if not url:
                    url = ''
                    err_msg = "StyleJSONURL for version '{0}' not defined for environment {1} - Trying VectorStyleJSON path {2}".format(
                        version, environmentConfig, stylejson_value)
                    log_sig_warn(err_msg, sigevent_url)

                stylejson.attributes['url'] = url
                stylejson.attributes['location'] = location

        # Similar treatment as VectorStyleJSON for VectorMetadataJSON
        metadatajson_elems = dom.getElementsByTagName('MetadataJSON')
        metadatajson_elems.extend(dom.getElementsByTagName('VectorMetadataJSON'))
        metadatajsons = []
        for metadatajson in metadatajson_elems:
            if metadatajson.firstChild:
                metadatajsons.append(metadatajson)

        # Set default VectorMetadataJSON
        default_metadatajson = None
        if metadatajsons:
            if len(metadatajsons) == 1:
                default_metadatajson = metadatajsons[0]
            else:
                for metadatajson in metadatajsons:
                    if 'default' in metadatajson.attributes.keys(
                    ) and metadatajson.attributes['default'].value == 'true':
                        if default_metadatajson is not None:
                            err_msg = 'Multiple <VectorMetadataJSON> elements have "default=true" attribute but only one is allowed, using ' + metadatajson.toxml(
                            )
                            log_sig_err(err_msg, sigevent_url)
                        default_metadatajson = metadatajson
            if len(metadatajsons) > 1 and default_metadatajson is None:
                default_metadatajson = metadatajsons[-1]
                err_msg = 'Multiple <VectorMetadataJSON> elements but none have "default=true" attribute, using ' + default_metadatajson.toxml(
                )
                log_sig_err(err_msg, sigevent_url)

        # Match <MetadataJSONLocation> and <MetadataJSONURL> to metadata json files with the same version and set them as attributes of the <VectorMetadataJSON>
        if metadatajsons:
            for metadatajson in metadatajsons:
                if 'version' not in metadatajson.attributes.keys():
                    metadatajson.attributes['version'] = ''

                metadatajson_value = metadatajson.firstChild.nodeValue
                version = metadatajson.attributes['version'].value
                location = next(
                    (location.firstChild.nodeValue
                     for location in environment.metadatajson_dirs
                     if location.attributes['version'].value == version), None)
                url = next((url.firstChild.nodeValue
                            for url in environment.metadatajsonUrls
                            if url.attributes['version'].value == version),
                           None)

                if not location:
                    location = ''
                    err_msg = "MetadataJSONLocation for version '{0}' not defined for environment {1} - Trying VectorMetadataJSON path {2}".format(
                        version, environmentConfig, metadatajson_value)
                    log_sig_warn(err_msg, sigevent_url)

                if not url:
                    url = ''
                    err_msg = "MetadataJSONURL for version '{0}' not defined for environment {1} - Trying VectorMetadataJSON path {2}".format(
                        version, environmentConfig, metadatajson_value)
                    log_sig_warn(err_msg, sigevent_url)

                metadatajson.attributes['url'] = url
                metadatajson.attributes['location'] = location

        try:
            emptyTile = get_dom_tag_value(dom, 'EmptyTile')
        except:
            emptyTile = None
        try:
            if emptyTile == None:
                emptyTileOffset = dom.getElementsByTagName(
                    'EmptyTileSize')[0].attributes['offset'].value
            else:
                emptyTileOffset = dom.getElementsByTagName(
                    'EmptyTile')[0].attributes['offset'].value
        except:
            emptyTileOffset = 0

        # Patterns
        patterns = []
        rest_patterns = []
        patternTags = dom.getElementsByTagName('Pattern')
        for pattern in patternTags:
            try:
                if pattern.attributes[
                        'type'].value == "WMTS-REST":  # append WMTS REST patterns
                    rest_patterns.append(pattern.firstChild.data.strip())
                else:  # assume TWMS key-value pair
                    patterns.append(pattern.firstChild.data.strip())
            except KeyError:  # append if type does not exist
                patterns.append(pattern.firstChild.data.strip())

        # Time
        if configuration_time:
            times = configuration_time.split(',')
        else:
            times = []
            timeTags = dom.getElementsByTagName('Time')
            for time in timeTags:
                try:
                    times.append(time.firstChild.data.strip())
                except AttributeError:
                    times.append('')

        # Set End Points
        if environment.wmts_dir != None:
            wmtsEndPoint = environment.wmts_dir
        else:  # default projection dir
            wmtsEndPoint = lcdir + "/wmts/" + projection.id.replace(":", "")
        if environment.twms_dir != None:
            twmsEndPoint = environment.twms_dir
        else:
            # default projection dir
            twmsEndPoint = lcdir + "/twms/" + projection.id.replace(":", "")

        wmts_endpoints[wmtsEndPoint] = WMTSEndPoint(
            wmtsEndPoint, cacheLocation_wmts, cacheBasename_wmts,
            wmts_getCapabilities, projection)
        twms_endpoints[twmsEndPoint] = TWMSEndPoint(
            twmsEndPoint, cacheLocation_twms, cacheBasename_twms,
            twms_getCapabilities, getTileService, projection)
        wms_endpoints[environment.mapfileStagingLocation] = WMSEndPoint(
            environment.mapfileStagingLocation, environment.mapfileLocation,
            environment.mapfileLocationBasename,
            environment.mapfileConfigLocation,
            environment.mapfileConfigBasename)

        # Close file.
        config_file.close()

    log_info_mssg('config: Identifier: ' + identifier)
    log_info_mssg('config: Title: ' + title)
    log_info_mssg('config: FileNamePrefix: ' + fileNamePrefix)
    log_info_mssg('config: TiledGroupName: ' + tiledGroupName)
    log_info_mssg('config: Compression: ' + compression)
    log_info_mssg('config: TileMatrixSet: ' + tilematrixset)
    if wmsSourceLoc:
        log_info_mssg('config: WMSSourceLoc: ' + wmsSourceLoc)
    if wmsGroupName:
        log_info_mssg('config: WMSGroupName: ' + wmsGroupName)
    if wmsLayerGroupName:
        log_info_mssg('config: WMSLayerGroupName: ' + wmsLayerGroupName)
    if emptyTile:
        log_info_mssg('config: EmptyTile: ' + emptyTile)
    if str(emptyTileSize) != "":
        log_info_mssg('config: EmptyTileSize: ' + str(emptyTileSize))
    log_info_mssg('config: EmptyTileOffset: ' + str(emptyTileOffset))
    if headerFileName:
        log_info_mssg('config: HeaderFileName: ' + headerFileName)
    if archiveLocation:
        log_info_mssg('config: ArchiveLocation static=' + str(static) +
                      ' year=' + str(year) + ' subdaily=' + str(subdaily) +
                      ': ' + archiveLocation)
    if dataFileLocation:
        log_info_mssg('config: DataFileLocation: ' + dataFileLocation)
    if indexFileLocation:
        log_info_mssg('config: IndexFileLocation: ' + indexFileLocation)
    if zIndexFileLocation:
        log_info_mssg('config: ZIndexFileLocation: ' + zIndexFileLocation)
    if projection:
        log_info_mssg('config: Projection: ' + str(projection.id))
    if getTileService:
        log_info_mssg('config: GetTileServiceLocation: ' + str(getTileService))
    if wmts_getCapabilities:
        log_info_mssg('config: WMTS GetCapabilitiesLocation: ' +
                      str(wmts_getCapabilities))
    if twms_getCapabilities:
        log_info_mssg('config: TWMS GetCapabilitiesLocation: ' +
                      str(twms_getCapabilities))
    if cacheLocation_wmts:
        log_info_mssg('config: WMTS CacheLocation: ' + str(cacheLocation_wmts))
    if cacheLocation_twms:
        log_info_mssg('config: TWMS CacheLocation: ' + str(cacheLocation_twms))
    if cacheBasename_wmts:
        log_info_mssg('config: WMTS Basename: ' + str(cacheLocation_wmts))
    if cacheBasename_twms:
        log_info_mssg('config: TWMS Basename: ' + str(cacheLocation_twms))
    if wmtsEndPoint:
        log_info_mssg('config: WMTSEndPoint: ' + str(wmtsEndPoint))
    if twmsEndPoint:
        log_info_mssg('config: TWMSEndPoint: ' + str(twmsEndPoint))
    if tmsLimits:
        log_info_mssg('config: TileMatrixSetLimits: ' + tmsLimits.toxml())
    if colormaps:
        for colormap in colormaps:
            map_value = colormap.firstChild.nodeValue.strip()
            log_info_mssg('config: ColorMap: ' + str(map_value))
    if stylejsons:
        for stylejson in stylejsons:
            json_value = stylejson.firstChild.nodeValue.strip()
            log_info_mssg('config: VectorStyleJSON: ' + str(json_value))
    if metadatajsons:
        for metadatajson in metadatajsons:
            json_value = metadatajson.firstChild.nodeValue.strip()
            log_info_mssg('config: VectorMetadataJSON: ' + str(json_value))
    log_info_mssg('config: Patterns: ' + str(patterns))
    if len(rest_patterns) > 0:
        log_info_mssg('config: WMTS-REST Patterns: ' + str(rest_patterns))
    if len(times) > 0:
        log_info_mssg('config: Time: ' + str(times))

    if archiveLocation != None:
        archiveLocation = add_trailing_slash(archiveLocation)
        # check if absolute path or else use relative to cache location
        if archiveLocation[0] == '/':
            mrfLocation = archiveLocation
        else:
            mrfLocation = cacheConfig + archiveLocation
            archiveLocation = mrfLocation
    else:  # use archive location relative to cache if not defined
        mrfLocation = add_trailing_slash(cacheConfig)
    if year == True:
        if archiveLocation != None:
            mrfLocation = mrfLocation + 'YYYY/'
        else:
            mrfLocation = mrfLocation + fileNamePrefix + '/YYYY/'

    if static == True:
        mrf = mrfLocation + fileNamePrefix + '.mrf'
    else:
        if subdaily == True:
            mrf = mrfLocation + fileNamePrefix + 'TTTTTTTTTTTTT_.mrf'
        else:
            mrf = mrfLocation + fileNamePrefix + 'TTTTTTT_.mrf'

    if indexFileLocation == None:
        if archiveLocation != None and archiveLocation[0] == '/':
            # use absolute path of archive
            indexFileLocation = mrf.replace('.mrf', '.idx')
        else:
            # use relative path to cache
            indexFileLocation = mrf.replace(cacheConfig, '').replace(
                '.mrf', '.idx')

    if dataFileLocation == None:
        if archiveLocation != None and archiveLocation[0] == '/':
            # use absolute path of archive
            dataFileLocation = mrf
        else:
            # use relative path to cache
            dataFileLocation = mrf.replace(cacheConfig, '')
        if compression.lower() in ['jpg', 'jpeg']:
            dataFileLocation = dataFileLocation.replace('.mrf', '.pjg')
            mrf_format = 'image/jpeg'
        elif compression.lower() in ['tif', 'tiff']:
            dataFileLocation = dataFileLocation.replace('.mrf', '.ptf')
            mrf_format = 'image/tiff'
        elif compression.lower() in ['lerc']:
            dataFileLocation = dataFileLocation.replace('.mrf', '.lrc')
            mrf_format = 'image/lerc'
        elif compression.lower() in ['mvt']:
            compression = "MVT"
            dataFileLocation = dataFileLocation.replace('.mrf', '.pvt')
            mrf_format = 'application/vnd.mapbox-vector-tile'
        elif vectorType is not None:
            dataFileLocation = dataFileLocation.replace('.mrf', '.shp')
        else:
            dataFileLocation = dataFileLocation.replace('.mrf', '.ppg')
            mrf_format = 'image/png'

    if zIndexFileLocation == None:
        if archiveLocation != None and archiveLocation[0] == '/':
            # use absolute path of archive
            zIndexFileLocation = mrf
        else:
            # use relative path to cache
            zIndexFileLocation = mrf.replace(cacheConfig, '')
        zIndexFileLocation = zIndexFileLocation.replace('.mrf', '.zdb')

    # Parse header filename. Default is to use the 'mrf' filename.
    header_type = None
    header_file_name = mrf
    try:
        headerFileName = dom.getElementsByTagName('HeaderFileName')[0]
        header_file_name = get_dom_tag_value(dom, 'HeaderFileName')
    except (AttributeError, IndexError):
        pass
    try:
        header_type = headerFileName.getAttribute('type')
    except AttributeError:
        pass

    if not vectorType:
        # Open MRF header if one has been supplied (except if "type" attr is "prefix")
        header_dom = None
        if header_type != 'prefix':
            try:
                with open(header_file_name, 'r') as mrf_file:
                    try:
                        header_dom = xml.dom.minidom.parse(mrf_file)
                    except:
                        log_sig_err(
                            'Badly-formatted MRF header file: {0}'.format(
                                mrf_file), sigevent_url)
                        continue
            except IOError:
                log_sig_err(
                    "Can't open MRF file: {0}".format(header_file_name),
                    sigevent_url)
                continue

        # Create base MRF document. We'll be adding stuff from either the header MRF or the
        # layer config file to this.
        mrf_impl = xml.dom.minidom.getDOMImplementation()
        mrf_dom = mrf_impl.createDocument(None, 'MRF_META', None)
        mrf_meta = mrf_dom.documentElement

        # Create <Raster> tag
        raster_node = mrf_dom.createElement('Raster')

        # If the "prefix" attribute of <HeaderFileName> is present, we grab MRF stuff from the
        # layer config file. Otherwise, use the header file specified.
        if header_type == 'prefix':
            mrf_base = header_file_name + '.mrf'
            header_dom = dom
            log_info_mssg('Using MRF data within layer config file')
        else:
            log_info_mssg('Using MRF Archetype: ' + header_file_name)
            mrf_base = os.path.basename(header_file_name)

        if header_dom != None:
            # Check if <Size> tag present and has all 3 required values (x,y,c)
            try:
                size_node = header_dom.getElementsByTagName('Size')[0]
            except IndexError:
                log_sig_err(
                    "<Size> tag not present in MRF header file or layer config",
                    sigevent_url)
                continue
            if size_node != None:
                if not all(attr in size_node.attributes.keys()
                           for attr in ('x', 'y')):
                    log_sig_err("<Size> tag needs to have attributes x and y",
                                sigevent_url)
                    continue
                else:
                    raster_node.appendChild(size_node)
                    bands = size_node.getAttribute('c')

            # Create <Compression> node
            compression_node = mrf_dom.createElement('Compression')
            compression_text_node = mrf_dom.createTextNode(compression)
            compression_node.appendChild(compression_text_node)
            raster_node.appendChild(compression_node)

            # Check if <DataValues> tag is present and the NoData attribute is present
            try:
                datavalues_node = header_dom.getElementsByTagName(
                    'DataValues')[0]
            except IndexError:
                datavalues_node = None
            finally:
                if datavalues_node is not None:
                    raster_node.appendChild(datavalues_node)

            # Check if the <Quality> tag is present and of a valid type
            try:
                quality_node = header_dom.getElementsByTagName('Quality')[0]
            except IndexError:
                quality_node = None
            if quality_node is not None:
                if quality_node.firstChild.nodeValue >= 0 and quality_node.firstChild.nodeValue <= 100:
                    log_sig_err(
                        "<Quality> tag must be an integer between 1 and 100",
                        sigevent_url)
                    continue
                else:
                    raster_node.appendChild(quality_node)

            # Check if <PageSize> node is present and has c, x, and y attributes
            try:
                page_size_node = header_dom.getElementsByTagName('PageSize')[0]
            except IndexError:
                page_size_node = None
                log_sig_err(
                    "<PageSize> tag not present in MRF header file or layer config",
                    sigevent_url)
                continue
            if page_size_node is not None:
                if all(attr in page_size_node.attributes.keys()
                       for attr in ('x', 'y')):
                    raster_node.appendChild(page_size_node)
                else:
                    log_sig_err("<PageSize> requires x, and y attributes",
                                sigevent_url)
                    continue

            # Add <Raster> tag to MRF
            mrf_meta.appendChild(raster_node)

            # Create <Rsets>
            try:
                rsets_node = header_dom.getElementsByTagName('Rsets')[0]
            except IndexError:
                rsets_node = None
                log_sig_err(
                    "<Rsets> tag not present in layer config or MRF header file",
                    sigevent_url)
                continue
            if rsets_node is not None:
                try:
                    scale_attribute = rsets_node.getAttribute('scale')
                except:
                    log_sig_err("Attribute 'scale' not present in <Rsets> tag",
                                sigevent_url)
                    continue
                else:
                    try:
                        if scale_attribute:
                            if int(scale_attribute
                                   ) != projection.tilematrixsets[
                                       tilematrixset].scale:
                                log_sig_err(
                                    "Overview scales do not match - " +
                                    tilematrixset + ": " + str(
                                        str(projection.tilematrixsets[
                                            tilematrixset].scale)) + ", " +
                                    "Provided: " + scale_attribute,
                                    sigevent_url)
                                continue
                        if projection.tilematrixsets[tilematrixset].levels > 1:
                            rsets_node.setAttribute(
                                'scale',
                                str(projection.tilematrixsets[tilematrixset].
                                    scale))
                    except KeyError:
                        log_sig_err(
                            "Invalid TileMatrixSet " + tilematrixset +
                            " for projection " + projection.id, sigevent_url)
                        continue
                # Add data file locations
                dataFileNameElement = mrf_dom.createElement('DataFileName')
                dataFileNameElement.appendChild(
                    mrf_dom.createTextNode(dataFileLocation))
                indexFileNameElement = mrf_dom.createElement('IndexFileName')
                indexFileNameElement.appendChild(
                    mrf_dom.createTextNode(indexFileLocation))
                rsets_node.appendChild(dataFileNameElement)
                rsets_node.appendChild(indexFileNameElement)

            # Add zindex file name
            has_zdb = False
            if size_node.hasAttribute('z'):
                z_index_node = mrf_dom.createElement('ZIndexFileName')
                z_index_text_node = mrf_dom.createTextNode(zIndexFileLocation)
                z_index_node.appendChild(z_index_text_node)
                rsets_node.appendChild(z_index_node)
                has_zdb = True

            mrf_meta.appendChild(rsets_node)

            # Create GeoTags
            geotag_node = mrf_dom.createElement('GeoTags')
            # Check for bounding box
            try:
                bounding_box_node = header_dom.getElementsByTagName(
                    'BoundingBox')[0]
            except IndexError:
                bounding_box_node = None
                log_sig_err(
                    "<BoundingBox> tag not present in layer config or MRF header file",
                    sigevent_url)
                continue
            if bounding_box_node is not None:
                if all(attr in bounding_box_node.attributes.keys()
                       for attr in ('minx', 'miny', 'maxx', 'maxy')):
                    geotag_node.appendChild(bounding_box_node)
                else:
                    log_sig_err(
                        "<BoundingBox> requires minx, miny, maxx, and maxy attributes",
                        sigevent_url)
                    continue

            mrf_meta.appendChild(geotag_node)

        twms = mrf_dom.createElement('TWMS')
        levelsElement = mrf_dom.createElement('Levels')
        levelsElement.appendChild(
            mrf_dom.createTextNode(
                str(projection.tilematrixsets[tilematrixset].levels)))

        # Get page sizes for TWMS pattern and/or empty tile generation
        pageSize = mrf_dom.getElementsByTagName('PageSize')[0]
        tileX = int(pageSize.getAttribute('x'))
        tileY = int(pageSize.getAttribute('y'))

        if emptyTile != None:
            # Generate empty tile and override size if colormap is used
            if default_colormap != None and skip_empty_tiles == False:
                colormap_value = default_colormap.firstChild.nodeValue
                colormap_location = default_colormap.attributes[
                    'location'].value
                if colormap_location == '':
                    colormap_path = colormap_value
                else:
                    colormap_path = add_trailing_slash(
                        colormap_location) + colormap_value

                emptyTileSize = generate_empty_tile(colormap_path, emptyTile,
                                                    tileX, tileY)
            else:  # Override size if there is no colormap
                try:
                    # Get file size
                    print "\nReading empty tile file: " + emptyTile
                    emptyTileSize = os.path.getsize(emptyTile)
                    print "Empty tile size: " + str(emptyTileSize)
                except:
                    mssg = str().join(['Cannot read empty tile:  ', emptyTile])
                    log_sig_err(mssg, sigevent_url)

        emptyInfoElement = mrf_dom.createElement('EmptyInfo')
        emptyInfoElement.setAttribute('size', str(emptyTileSize))
        emptyInfoElement.setAttribute('offset', str(emptyTileOffset))
        twms.appendChild(levelsElement)
        twms.appendChild(emptyInfoElement)

        # No longer used
        #     if colormap:
        #         metadataElement = mrf_dom.createElement('Metadata')
        #         metadataElement.appendChild(mrf_dom.createTextNode(colormap))
        #         twms.appendChild(twms.appendChild(metadataElement))

        # add default TWMS patterns
        twms_time_pattern = "request=GetMap&layers=%s&srs=%s&format=%s&styles=&time=[-0-9]*&width=%s&height=%s&bbox=[-,\.0-9+Ee]*" % (
            identifier, str(projection.id), mrf_format.replace("/", "%2F"),
            str(tileX), str(tileY))
        twms_notime_pattern = "request=GetMap&layers=%s&srs=%s&format=%s&styles=&width=%s&height=%s&bbox=[-,\.0-9+Ee]*" % (
            identifier, str(projection.id), mrf_format.replace("/", "%2F"),
            str(tileX), str(tileY))
        patterns.append(twms_time_pattern)
        patterns.append(twms_notime_pattern)

        patternElements = []
        for pattern in patterns:
            patternElements.append(mrf_dom.createElement('Pattern'))
            patternElements[-1].appendChild(
                mrf_dom.createCDATASection(pattern))

        for patternElement in patternElements:
            twms.appendChild(patternElement)

        # Time elements
        detected_times = []
        if static == False:
            for time in times:
                detected_times += detect_time(time, archiveLocation,
                                             fileNamePrefix, year, has_zdb)

            timeElements = []
            for detected_time in detected_times:
                timeElements.append(mrf_dom.createElement('Time'))
                timeElements[-1].appendChild(
                    mrf_dom.createTextNode(detected_time))

            for timeElement in timeElements:
                twms.appendChild(timeElement)

        mrf_meta.appendChild(twms)

        if projection:
            projectionElement = mrf_dom.createElement('Projection')
            projectionElement.appendChild(
                mrf_dom.createCDATASection(projection.wkt))
            mrf_meta.appendChild(projectionElement)

        if not os.path.exists(twmsEndPoint):
            os.makedirs(twmsEndPoint)
        if not os.path.exists(wmtsEndPoint):
            os.makedirs(wmtsEndPoint)

        twms_mrf_filename = twmsEndPoint + '/' + mrf_base
        twms_mrf_file = open(twms_mrf_filename, 'w+')

        formatted_xml = get_pretty_xml(mrf_dom)
        twms_mrf_file.write(formatted_xml)
        twms_mrf_file.seek(0)

        wmts_mrf_filename = wmtsEndPoint + '/' + mrf_base
        # check if file already exists and has same TileMatrixSet, if not then create another file
        if os.path.isfile(wmts_mrf_filename):
            wmts_mrf_file = open(wmts_mrf_filename, 'r')
            if tilematrixset not in wmts_mrf_file.read():
                log_sig_warn(
                    tilematrixset + " not found in existing " +
                    wmts_mrf_filename +
                    ". Creating new file for TileMatrixSet.", sigevent_url)
                wmts_mrf_filename = wmts_mrf_filename.split(
                    ".mrf")[0] + "_" + tilematrixset + ".mrf"
            wmts_mrf_file.close()

        wmts_mrf_file = open(wmts_mrf_filename, 'w+')
        lines = twms_mrf_file.readlines()

        # change patterns for WMTS
        pattern_replaced = False
        try:
            if is_encoded:
                wmts_pattern = "<![CDATA[SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=%s&STYLE=(default|encoded)?&TILEMATRIXSET=%s&TILEMATRIX=[0-9]*&TILEROW=[0-9]*&TILECOL=[0-9]*&FORMAT=%s]]>" % (
                    identifier, tilematrixset, mrf_format.replace("/", "%2F"))
            else:
                wmts_pattern = "<![CDATA[SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=%s&STYLE=(default)?&TILEMATRIXSET=%s&TILEMATRIX=[0-9]*&TILEROW=[0-9]*&TILECOL=[0-9]*&FORMAT=%s]]>" % (
                    identifier, tilematrixset, mrf_format.replace("/", "%2F"))
        except KeyError:
            log_sig_exit(
                'ERROR', 'TileMatrixSet ' + tilematrixset +
                ' not found for projection: ' + projection.id, sigevent_url)
        for line in lines:
            if '<Pattern>' in line:
                if pattern_replaced == False:
                    patternline = line.split('Pattern')
                    line = patternline[
                        0] + "Pattern>" + wmts_pattern + "</Pattern" + patternline[
                            -1]
                    pattern_replaced = True
                else:
                    line = ''
            wmts_mrf_file.write(line)

        twms_mrf_file.close()
        wmts_mrf_file.seek(0)
        wmts_mrf_file.close()
        try:
            mrf_file.close()
        except:
            pass

        print '\n' + twms_mrf_filename + ' configured successfully\n'
        print '\n' + wmts_mrf_filename + ' configured successfully\n'

        # generate legend if requested
        legendUrl_svg_v_meta = ''
        legendUrl_svg_h_meta = ''
        legendUrl_png_h_url  = None
        if legend and default_colormap:
            colormap_value = default_colormap.firstChild.nodeValue
            colormap_location = default_colormap.attributes['location'].value
            if colormap_location == '':
                colormap_path = colormap_value
            else:
                colormap_path = add_trailing_slash(
                    colormap_location) + colormap_value

            legend_identifier = os.path.splitext(colormap_value)[0]
            legend_output = ''
            try:
                legend_output = environment.legend_dir + legend_identifier
            except:
                message = "Legend directory has not been defined for environment with cache location: " + environment.cache
                log_sig_err(message, sigevent_url)

            try:
                if environment.legendUrl != None:
                    if legend_output != '':
                        # These URLs _are_ used in the WMTS capabilities
                        legendUrl_svg_v_meta, legendUrl_svg_v_width, legendUrl_svg_v_height = generate_legend(
                            colormap_path, legend_output + '_V.svg',
                            environment.legendUrl + legend_identifier + '_V.svg',
                            'svg', 'vertical')
                        legendUrl_svg_h_meta, legendUrl_svg_h_width, legendUrl_svg_h_height = generate_legend(
                            colormap_path, legend_output + '_H.svg',
                            environment.legendUrl + legend_identifier + '_H.svg',
                            'svg', 'horizontal')

                        # This URL _is not_ used in the WMTS capabilities
                        legendUrl_png_h_meta, legendUrl_png_h_width, legendUrl_png_h_height = generate_legend(
                            colormap_path, legend_output + '_H.png',
                            environment.legendUrl + legend_identifier + '_H.png',
                            'png', 'horizontal')
                        # saving this for later since WMS doesn't use the <LegendURL> metadata tag
                        legendUrl_png_h_url = environment.legendUrl + legend_identifier + '_H.png'
                else:
                    message = "Legend URL has not been defined for environment with cache location: " + environment.cache
                    log_sig_err(message, sigevent_url)
            except:
                message = "Error generating legend for " + legend_identifier
                log_sig_err(message, sigevent_url)

    else:  # Vectors
        # Vectors aren't supporting z-slices
        has_zdb = False

        # Detect times for product based on layer configuration <Time> elements
        detected_times = []
        if static == False:
            for time in times:
                detected_times += detect_time(time, archiveLocation, fileNamePrefix, year, has_zdb)

        # Clear legend variables
        legendUrl_svg_v_meta = ''
        legendUrl_svg_h_meta = ''
        legendUrl_png_h_url  = None


    # generate archive links if requested
    if links:
        if len(detected_times) > 0:
            print "Generating archive links for " + fileNamePrefix
            generate_links(detected_times, archiveLocation, fileNamePrefix,
                           year, dataFileLocation, has_zdb)
        else:
            print fileNamePrefix + " is not a time varying layer"

# Modify service files

#getCapabilities TWMS
    if no_twms == False:
        try:
            # Copy and open base GetCapabilities.
            getCapabilities_file = twmsEndPoint + '/getCapabilities.xml'
            shutil.copyfile(lcdir + '/conf/getcapabilities_base_twms.xml',
                            getCapabilities_file)
            getCapabilities_base = open(getCapabilities_file, 'r+')
        except IOError:
            mssg = str().join([
                'Cannot read getcapabilities_base_twms.xml file:  ',
                lcdir + '/conf/getcapabilities_base_twms.xml'
            ])
            log_sig_exit('ERROR', mssg, sigevent_url)
        else:
            lines = getCapabilities_base.readlines()
            for idx in range(0, len(lines)):
                if '<SRS></SRS>' in lines[idx]:
                    lines[idx] = lines[idx].replace(
                        '<SRS></SRS>', '<SRS>' + projection.id + '</SRS>')
                if '<CRS></CRS>' in lines[idx]:
                    lines[idx] = lines[idx].replace(
                        '<CRS></CRS>', '<CRS>' + projection.id + '</CRS>')
                if 'OnlineResource' in lines[idx]:
                    spaces = lines[idx].index('<')
                    onlineResource = xml.dom.minidom.parseString(
                        lines[idx]).getElementsByTagName('OnlineResource')[0]
                    if 'KeywordList' in lines[idx - 1]:
                        onlineResource.attributes[
                            'xlink:href'] = twmsServiceUrl  # don't include the cgi portion
                    else:
                        onlineResource.attributes[
                            'xlink:href'] = twmsServiceUrl + "twms.cgi?"
                    lines[idx] = (' ' * spaces) + onlineResource.toprettyxml(
                        indent=" ")
            getCapabilities_base.seek(0)
            getCapabilities_base.truncate()
            getCapabilities_base.writelines(lines)
            getCapabilities_base.close()

        #getTileService
    if no_twms == False:
        try:
            # Copy and open base GetTileService.
            getTileService_file = twmsEndPoint + '/getTileService.xml'
            shutil.copyfile(lcdir + '/conf/gettileservice_base.xml',
                            getTileService_file)
            getTileService_base = open(getTileService_file, 'r+')
        except IOError:
            mssg = str().join([
                'Cannot read gettileservice_base.xml file:  ',
                lcdir + '/conf/gettileservice_base.xml'
            ])
            log_sig_exit('ERROR', mssg, sigevent_url)
        else:
            lines = getTileService_base.readlines()
            for idx in range(0, len(lines)):
                if 'BoundingBox' in lines[idx]:
                    lines[idx] = lines[idx].replace(
                        "BoundingBox", "LatLonBoundingBox").replace(
                            "{minx}", projection.lowercorner[0]).replace(
                                "{miny}", projection.lowercorner[1]).replace(
                                    "{maxx}",
                                    projection.uppercorner[0]).replace(
                                        "{maxy}", projection.uppercorner[1])
                if 'OnlineResource' in lines[idx]:
                    spaces = lines[idx].index('<')
                    onlineResource = xml.dom.minidom.parseString(
                        lines[idx]).getElementsByTagName('OnlineResource')[0]
                    if 'KeywordList' in lines[idx - 1]:
                        onlineResource.attributes[
                            'xlink:href'] = twmsServiceUrl  # don't include the cgi portion
                    else:
                        onlineResource.attributes[
                            'xlink:href'] = twmsServiceUrl + "twms.cgi?"
                    lines[idx] = (' ' * spaces) + onlineResource.toprettyxml(
                        indent=" ")
            getTileService_base.seek(0)
            getTileService_base.truncate()
            getTileService_base.writelines(lines)
            getTileService_base.close()

    #getCapabilities WMTS modify Service URL
    if no_wmts == False:
        try:
            # Copy and open base GetCapabilities.
            getCapabilities_file = wmtsEndPoint + '/getCapabilities.xml'
            shutil.copyfile(lcdir + '/conf/getcapabilities_base_wmts.xml',
                            getCapabilities_file)
            getCapabilities_base = open(getCapabilities_file, 'r+')
        except IOError:
            mssg = str().join([
                'Cannot read getcapabilities_base_wmts.xml file:  ',
                lcdir + '/conf/getcapabilities_base_wmts.xml'
            ])
            log_sig_exit('ERROR', mssg, sigevent_url)
        else:
            lines = getCapabilities_base.readlines()
            for idx in range(0, len(lines)):
                if '<ows:Get' in lines[idx]:
                    spaces = lines[idx].index('<')
                    getUrlLine = lines[idx].replace(
                        'ows:Get',
                        'Get xmlns:xlink="http://www.w3.org/1999/xlink"'
                    ).replace('>', '/>')
                    getUrl = xml.dom.minidom.parseString(
                        getUrlLine).getElementsByTagName('Get')[0]
                    if '1.0.0/WMTSCapabilities.xml' in lines[idx]:
                        getUrl.attributes[
                            'xlink:href'] = wmtsServiceUrl + '1.0.0/WMTSCapabilities.xml'
                    elif 'wmts.cgi?' in lines[idx]:
                        getUrl.attributes[
                            'xlink:href'] = wmtsServiceUrl + 'wmts.cgi?'
                    else:
                        getUrl.attributes['xlink:href'] = wmtsServiceUrl
                    lines[idx] = (' ' * spaces) + getUrl.toprettyxml(
                        indent=" ").replace('Get', 'ows:Get').replace(
                            ' xmlns:xlink="http://www.w3.org/1999/xlink"',
                            '').replace('/>', '>')
                if 'ServiceMetadataURL' in lines[idx]:
                    spaces = lines[idx].index('<')
                    serviceMetadataUrlLine = lines[idx].replace(
                        'ServiceMetadataURL',
                        'ServiceMetadataURL xmlns:xlink="http://www.w3.org/1999/xlink"'
                    )
                    serviceMetadataUrl = xml.dom.minidom.parseString(
                        serviceMetadataUrlLine).getElementsByTagName(
                            'ServiceMetadataURL')[0]
                    serviceMetadataUrl.attributes[
                        'xlink:href'] = wmtsServiceUrl + '1.0.0/WMTSCapabilities.xml'
                    lines[idx] = (
                        ' ' * spaces
                    ) + serviceMetadataUrl.toprettyxml(indent=" ").replace(
                        ' xmlns:xlink="http://www.w3.org/1999/xlink"', '')
            getCapabilities_base.seek(0)
            getCapabilities_base.truncate()
            getCapabilities_base.writelines(lines)
            getCapabilities_base.close()

    # create WMTS layer metadata for GetCapabilities
    if no_wmts == False and vectorType is None:
        try:
            # Open layer XML file
            layer_xml = open(wmts_mrf_filename.replace('.mrf', '.xml'), 'w+')
        except IOError:
            mssg = str().join([
                'Cannot read layer XML file:  ',
                wmts_mrf_filename.replace('.mrf', '.xml')
            ])
            log_sig_exit('ERROR', mssg, sigevent_url)

        wmts_layer_template = """<Layer>
            <ows:Title xml:lang=\"en\">$Title</ows:Title>
            $BoundingBox
            <ows:Identifier>$Identifier</ows:Identifier>
            <ows:Metadata xlink:type="simple" xlink:role="http://earthdata.nasa.gov/gibs/metadata-type/colormap$MapVersion" xlink:href="$ColorMap" xlink:title="GIBS Color Map: Data - RGB Mapping"/>
            <ows:Metadata xlink:type="simple" xlink:role="http://earthdata.nasa.gov/gibs/metadata-type/mapbox-gl-style$MapVersion" xlink:href="$VectorStyleJSON" xlink:title="Mapbox GL Layer Styles"/>
            <ows:Metadata xlink:type="simple" xlink:role="http://earthdata.nasa.gov/gibs/metadata-type/layer$MapVersion" xlink:href="$VectorMetadataJSON" xlink:title="Layer Vector Metadata"/>
            <Style isDefault="true">
                <ows:Title xml:lang=\"en\">default</ows:Title>
                <ows:Identifier>default</ows:Identifier>
                $LegendURL_vertical
                $LegendURL_horizontal
            </Style>
            <Format>$Format</Format>
            <Dimension>
                <ows:Identifier>Time</ows:Identifier>
                <ows:UOM>ISO8601</ows:UOM>
                <Default>$DefaultDate</Default>
                <Current>false</Current>
                <Value>$DateRange</Value>
            </Dimension>
            <TileMatrixSetLink>
                <TileMatrixSet>$TileMatrixSet</TileMatrixSet>$TMSLimits
            </TileMatrixSetLink>
            <ResourceURL format="$Format" resourceType="tile" template="$WMTSServiceURL$Identifier/default/{Time}/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.$FileType"/>
        </Layer>"""

        layer_output = ""
        lines = wmts_layer_template.splitlines(True)
        for line in lines:
            # replace lines in template
            if '<Layer>' in line:
                line = '         ' + line
            if '</Layer>' in line:
                line = ' ' + line + '\n'
            if '$Title' in line:
                line = line.replace("$Title", title)
            if '$BoundingBox' in line:
                line = line.replace("$BoundingBox", projection.bbox_xml)
            if '$Identifier' in line:
                line = line.replace("$Identifier", identifier)
            if '$LegendURL_vertical' in line:
                line = line.replace("$LegendURL_vertical", legendUrl_svg_v_meta)
            if '$LegendURL_horizontal' in line:
                line = line.replace("$LegendURL_horizontal", legendUrl_svg_h_meta)
            if '$ColorMap' in line:
                if colormaps == None or default_colormap == None:
                    line = ''
                else:
                    line_template = line
                    # First create line for default colormap
                    if default_colormap.attributes['url'].value != '':
                        default_colormap_url = add_trailing_slash(
                            default_colormap.attributes['url'].
                            value) + default_colormap.firstChild.nodeValue
                    else:
                        default_colormap_url = default_colormap.firstChild.nodeValue
                    line = line.replace("$MapVersion", '')
                    line = line.replace("$ColorMap", default_colormap_url)
                    # Add rest of tags
                    if default_colormap.attributes['version'].value != '':
                        for colormap in colormaps:
                            if colormap.attributes['url'].value != '':
                                colormap_url = add_trailing_slash(
                                    colormap.attributes['url'].
                                    value) + colormap.firstChild.nodeValue
                            else:
                                colormap_url = colormap.firstChild.nodeValue
                            newline = line_template.replace(
                                "$MapVersion",
                                '/' + colormap.attributes['version'].value)
                            newline = newline.replace("$ColorMap",
                                                      colormap_url)
                            line += newline[3:]
            if '$VectorStyleJSON' in line:
                if stylejsons == None or default_stylejson == None:
                    line = ''
                else:
                    line_template = line
                    # First create line for default style
                    if default_stylejson.attributes['url'].value != '':
                        default_stylejson_url = add_trailing_slash(
                            default_stylejson.attributes['url'].
                            value) + default_stylejson.firstChild.nodeValue
                    else:
                        default_stylejson_url = default_stylejson.firstChild.nodeValue
                    line = line.replace("$MapVersion", '')
                    line = line.replace("$VectorStyleJSON", default_stylejson_url)
                    # Add rest of tags
                    if default_stylejson.attributes['version'].value != '':
                        for stylejson in stylejsons:
                            if stylejson.attributes['url'].value != '':
                                stylejson_url = add_trailing_slash(
                                    stylejson.attributes['url'].
                                    value) + stylejson.firstChild.nodeValue
                            else:
                                stylejson_url = stylejson.firstChild.nodeValue
                            newline = line_template.replace(
                                "$MapVersion",
                                '/' + stylejson.attributes['version'].value)
                            newline = newline.replace("$VectorStyleJSON",
                                                      stylejson_url)
                            line += newline[3:]
            if '$VectorMetadataJSON' in line:
                if metadatajsons == None or default_metadatajson == None:
                    line = ''
                else:
                    line_template = line
                    # First create line for default metadata
                    if default_metadatajson.attributes['url'].value != '':
                        default_metadatajson_url = add_trailing_slash(
                            default_metadatajson.attributes['url'].
                            value) + default_metadatajson.firstChild.nodeValue
                    else:
                        default_metadatajson_url = default_metadatajson.firstChild.nodeValue
                    line = line.replace("$MapVersion", '')
                    line = line.replace("$VectorMetadataJSON",
                                        default_metadatajson_url)
                    # Add rest of tags
                    if default_metadatajson.attributes['version'].value != '':
                        for metadatajson in metadatajsons:
                            if metadatajson.attributes['url'].value != '':
                                metadatajson_url = add_trailing_slash(
                                    metadatajson.attributes['url'].
                                    value) + metadatajson.firstChild.nodeValue
                            else:
                                metadatajson_url = metadatajson.firstChild.nodeValue
                            newline = line_template.replace(
                                "$MapVersion",
                                '/' + metadatajson.attributes['version'].value)
                            newline = newline.replace("$VectorMetadataJSON",
                                                      metadatajson_url)
                            line += newline[3:]
            if '$Format' in line:
                line = line.replace("$Format", mrf_format)
            if '$FileType' in line:
                if mrf_format == "application/vnd.mapbox-vector-tile":
                    line = line.replace("$FileType", "mvt")
                else:
                    line = line.replace("$FileType", mrf_format.split('/')[1])
            if '$WMTSServiceURL' in line:
                line = line.replace("$WMTSServiceURL",
                                    environment.wmtsServiceUrl)
            if '$TileMatrixSet' in line:
                line = line.replace("$TileMatrixSet", tilematrixset)
                if tmsLimits:
                    line = line.replace('$TMSLimits', tmsLimits.toxml())
                else:
                    line = line.replace('$TMSLimits', '')
                tilematrixset_line = line

            if static == True or len(detected_times) == 0:
                if any(x in line for x in [
                        'Dimension', '<ows:Identifier>Time</ows:Identifier>',
                        '<ows:UOM>ISO8601</ows:UOM>', '$DefaultDate',
                        '<Current>false</Current>', '$DateRange'
                ]):
                    line = ''
                if '/{Time}' in line:
                    line = line.replace('/{Time}', '')
            else:
                if '$DefaultDate' in line:
                    defaultDate = ''
                    for detected_time in detected_times:
                        defaultDate = detected_time.strip().split('/')[1]
                    line = line.replace("$DefaultDate", defaultDate)
                if '$DateRange' in line:
                    line = line.replace("$DateRange", detected_times[0].strip())
                    iterTime = iter(detected_times)
                    next(iterTime)
                    for detected_time in iterTime:
                        line += "             <Value>" + detected_time + "</Value>\n"
            # remove extra white space from lines
            line = line[3:]
            layer_output = layer_output + line
        # Replace extra lines before </Style>
        blanks = """

"""
        layer_output = layer_output.replace(blanks, "")
        # Check if additional encoded style is needed
        if is_encoded == True:
            style_encoded = """</Style>
         <Style isDefault="false">
            <ows:Title xml:lang=\"en\">encoded</ows:Title>
            <ows:Identifier>encoded</ows:Identifier>
         </Style>"""
            layer_output = layer_output.replace("</Style>", style_encoded)
        layer_xml.writelines(layer_output)

        # special case, add additional tilematrixsets from existing file and then remove
        existing_layer_xml_filename = wmts_mrf_filename.replace(
            '.mrf', '.xml').replace("_" + tilematrixset, '')
        if tilematrixset in wmts_mrf_filename:
            try:
                # Open GetCapabilities.
                existing_layer_xml = open(existing_layer_xml_filename, 'r+')
                lines = existing_layer_xml.readlines()
                os.remove(existing_layer_xml_filename)
                for idx in range(0, len(lines)):
                    if '<TileMatrixSet>' in lines[idx]:
                        lines[idx] = lines[idx] + tilematrixset_line
                layer_xml.seek(0)
                layer_xml.writelines(lines)
                existing_layer_xml.close()
            except:
                mssg = str().join([
                    'Cannot read existing layer XML file:  ',
                    existing_layer_xml_filename
                ])
                log_sig_err(mssg, sigevent_url)

        # close new file
        layer_xml.close()

    # create TWMS layer metadata for GetCapabilities
    if not no_twms and vectorType is None:
        try:
            # Open layer XML file
            layer_xml = open(twms_mrf_filename.replace('.mrf', '_gc.xml'), 'w+')
        except IOError:
            mssg = str().join([
                'Cannot read layer XML file:  ',
                twms_mrf_filename.replace('.mrf', '_gc.xml')
            ])
            log_sig_exit('ERROR', mssg, sigevent_url)

        TWMS_GC_LAYER_TEMPLATE = """    <Layer queryable=\"0\">
      <Name>{Identifier}</Name>
      <Title xml:lang=\"en\">{Title}</Title>
      <Abstract xml:lang=\"en\">{Abstract}</Abstract>
      <LatLonBoundingBox minx=\"{minx}\" miny=\"{miny}\" maxx=\"{maxx}\" maxy=\"{maxy}\" />
      <Style>
        <Name>default</Name> 
        <Title xml:lang=\"en\">(default) Default style</Title>
      </Style>
      <ScaleHint min=\"10\" max=\"100\"/> 
      <MinScaleDenominator>100</MinScaleDenominator>
      </Layer>
"""

        layer_output = bulk_replace(TWMS_GC_LAYER_TEMPLATE, [('{Identifier}', identifier),
                                                             ('{Title}', title),
                                                             ('{Abstract}', abstract),
                                                             ('{minx}', projection.lowercorner[0]),
                                                             ('{miny}', projection.lowercorner[1]),
                                                             ('{maxx}', projection.uppercorner[0]),
                                                             ('{maxy}', projection.uppercorner[1])])
        layer_xml.writelines(layer_output)
        layer_xml.close()

    # create TWMS layer metadata for GetTileService
    if not no_twms and vectorType is None:

        TWMS_GTS_LAYER_TEMPLATE = """<TiledGroup>
    <Name>{TiledGroupName}</Name>
    <Title xml:lang=\"en\">{Title}</Title>
    <Abstract xml:lang=\"en\">{Abstract}</Abstract>
    <Projection>{Projection}</Projection>
    <Pad>0</Pad>
    <Bands>{Bands}</Bands>
    <LatLonBoundingBox minx=\"{minx}\" miny=\"{miny}\" maxx=\"{maxx}\" maxy=\"{maxy}\" />
    <Key>${time}</Key>
{Patterns}</TiledGroup>
"""

        patterns = ""
        cmd = depth + '/oe_create_cache_config -p ' + twms_mrf_filename
        try:
            print '\nRunning command: ' + cmd
            process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            process.wait()
            for output in process.stdout:
                patterns = patterns + output
        except:
            log_sig_err("Error running command " + cmd, sigevent_url)

        layer_output = bulk_replace(TWMS_GTS_LAYER_TEMPLATE, [('{TiledGroupName}', tiledGroupName),
                                                              ('{Title}', title),
                                                              ('{Abstract}', abstract),
                                                              ('{Projection}', projection.wkt),
                                                              ('{Bands}', "4" if mrf_format == 'image/png' else bands),
                                                              ('{minx}', projection.lowercorner[0]),
                                                              ('{miny}', projection.lowercorner[1]),
                                                              ('{maxx}', projection.uppercorner[0]),
                                                              ('{maxy}', projection.uppercorner[1]),
                                                              ('{Patterns}', patterns)])


        # Write out the GTS XML file
        try:
            layer_xml = open(twms_mrf_filename.replace('.mrf', '_gts.xml'), 'w+')
            layer_xml.writelines(layer_output)
            layer_xml.close()
        except IOError:
            mssg = str().join(['Cannot read layer XML file:  ', twms_mrf_filename.replace('.mrf', '_gts.xml') ])
            log_sig_exit('ERROR', mssg, sigevent_url)


    # Create mapfile if requested and this is not a vector tile product
    if create_mapfile and compression != "MVT" and environment.mapfileStagingLocation is not None:

        # This block of code will check to see if we can just reuse an existing mapfile generated during the reproject
        # configuration. The reproject WMTS-sourced configuration is better, as it handles time snapping correctly.
        #
        # However, for now we are turning this feature off as there are implications regarding performance that need
        # additional testing.
        #
        # Note: oe_configure_remote_layers generates its own mapfiles based on the include/exclude rules and those should
        # be mutually exclusive with the layers configured here.
        reuse_wm_mapfiles = False
        mapfile_name = os.path.join(environment.mapfileStagingLocation, identifier + '.map')
        wm_layer_mapfile = os.path.join(environment.mapfileStagingLocation[:-5] + '3857', identifier + '.map')
        if reuse_wm_mapfiles and os.path.exists(wm_layer_mapfile) and projection.id != 'EPSG:3857' and mapfile_name != wm_layer_mapfile:
            # Vector layers will be ignored as they aren't reprojected
            print('Found, and using, existing reproject mapfile ' + wm_layer_mapfile)
            print('Copying to ' + mapfile_name)
            shutil.copyfile(wm_layer_mapfile, mapfile_name)
        elif wmsSourceLoc == "Remote":
            # Use the template to create the new Mapfile snippet
            wms_layer_group_info = ''
            dimension_info = ''
            validation_info = ''
            style_info = ''

            if wmsLayerGroupName is not None:
                wms_layer_group_info = bulk_replace(WMS_LAYER_GROUP_TEMPLATE,
                                                    [('{wms_layer_group}', wmsLayerGroupName)])

            if not static and len(detected_times) > 0:
                defaultDateTime = ''
                timeExtent      = ''
                for detected_time in detected_times:
                    defaultDateTime = detected_time.strip().split('/')[1]
                    timeExtent      = timeExtent + detected_time.strip() + ","

                dimension_info = bulk_replace(DIMENSION_TEMPLATE, [('{periods}', timeExtent.rstrip(',')),
                                                                   ('{default}', defaultDateTime)])
                validation_info = VALIDATION_TEMPLATE.replace('{default}', defaultDateTime)

            if legend and legendUrl_png_h_url:
                style_info = bulk_replace(STYLE_TEMPLATE, [('{width}', str(legendUrl_png_h_width)),
                                                           ('{height}', str(legendUrl_png_h_height)),
                                                           ('{href}', legendUrl_png_h_url)])

            src_epsg_code = str(projection.id.lower().split(":")[1])

            target_bbox   = [projection.lowercorner[0], projection.lowercorner[1],
                             projection.uppercorner[0], projection.uppercorner[1]]

            resource_url_template = "$WMTSServiceURL$Identifier/default/{Time}/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.$FileType"
            template_string       = bulk_replace(resource_url_template,
                                                 [('$WMTSServiceURL', environment.wmtsServiceUrl),
                                                  ('$Identifier', identifier),
                                                  ('$FileType', mrf_format.split('/')[1])])

            mapfile_snippet = bulk_replace(
                MAPFILE_TEMPLATE,
                [('{layer_name}', identifier),
                 ('{data_xml}', make_gdal_tms_xml(None, (4 if mrf_format == 'image/png' else 3), src_epsg_code,
                                                  tms=tilematrixset, template_string=template_string)),
                 ('{layer_title}', cgi.escape(title)),
                 ('{wms_layer_group_info}', wms_layer_group_info), ('{dimension_info}', dimension_info),
                 ('{style_info}', style_info), ('{validation_info}', validation_info),
                 ('{src_epsg}', src_epsg_code), ('{target_epsg}', src_epsg_code),
                 ('{target_bbox}', ' '.join(target_bbox))])

            if os.path.exists(mapfile_name):
                # Warn that we're overwriting a recently modified file
                last_mod = os.path.getmtime(mapfile_name)
                if tm() - last_mod <= 600:
                    log_sig_warn("Overwriting layer mapfile " + mapfile_name, sigevent_url)
            with open(mapfile_name, 'w+') as mapfile:
                mapfile.write(mapfile_snippet)

        else: # Create custom layer mapfile with time metadata elements
            # Write mapfile info for layer
            if os.path.exists(mapfile_name):
                # Warn that we're overwriting a recently modified file
                last_mod = os.path.getmtime(mapfile_name)
                if tm() - last_mod <= 600:
                    log_sig_warn("Overwriting layer mapfile " + mapfile_name, sigevent_url)
            with open(mapfile_name, 'w+') as mapfile:
                # Initialize validation values
                timeDirPattern = "%" + identifier + "_TIME%_" if not subdaily else "%" + identifier + "_TIME%"
                timeParamRegex = '"^([0-9]|T){7}$"'
                yearDirPattern = "%" + identifier + "_YEAR%"
                yearDirRegex = '"^([0-9]|Y){4}$"'
                subdailyDirPattern = "%" + identifier + "_SUBDAILY%_"
                subdailyParamRegex = '"^([0-9]|T){6}$"'

                minx = projection.lowercorner[0]
                miny = projection.lowercorner[1]
                maxx = projection.uppercorner[0]
                maxy = projection.uppercorner[1]

                # Write mapfile lines
                mapfile.write("LAYER\n")
                mapfile.write("\tNAME\t\"" + identifier + "\"\n")

                # If we're grouping layers together... (This is not the hierarchical wms_layer_group)
                if wmsGroupName:
                    # The default time/year needs to be empty because the DATA pattern will contain variables
                    # for both this layer _and_ its group.  If not "", then you get path elements like "YYYY2020"
                    default_time = ""
                    default_year = ""
                    default_subdaily = ""
                    timeDirPattern = ("%" + wmsGroupName + "_TIME%") + timeDirPattern
                    yearDirPattern = yearDirPattern + "%" + wmsGroupName + "_YEAR%"
                else:
                    default_time = "TTTTTTT"
                    default_year = "YYYY"
                    default_subdaily = "TTTTTT"

                if vectorType:
                    layer_type = vectorType.upper()
                else:
                    layer_type = 'RASTER'

                mapfile.write("\tTYPE\t" + layer_type + "\n")
                mapfile.write("\tSTATUS\tON\n")
                mapfile.write("\tVALIDATION\n")
                # The validation was previously being put in the layer METADATA -- deprecated in Mapserver 5.4.0
                if not static:
                    mapfile.write("\t\t\"default_" + identifier + "_TIME\"\t\t\"" + default_time + "\"\n")
                    mapfile.write("\t\t\"" + identifier + "_TIME\"\t\t\t" + timeParamRegex + "\n")
                    if wmsGroupName:
                        mapfile.write("\t\t\"default_" + wmsGroupName + "_TIME\"\t\t\"" + default_time + "\"\n")
                        mapfile.write("\t\t\"" + wmsGroupName + "_TIME\"\t\t\t" + timeParamRegex + "\n")
                if not static and year:
                    mapfile.write("\t\t\"default_" + identifier + "_YEAR\"\t\"" + default_year + "\"\n")
                    mapfile.write("\t\t\"" + identifier + "_YEAR\"\t\t" + yearDirRegex + "\n")
                    if wmsGroupName:
                        mapfile.write("\t\t\"default_" + wmsGroupName + "_YEAR\"\t\"" + default_year + "\"\n")
                        mapfile.write("\t\t\"" + wmsGroupName + "_YEAR\"\t\t" + yearDirRegex + "\n")
                if not static and subdaily:
                    mapfile.write("\t\t\"default_" + identifier + "_SUBDAILY\"\t\"" + default_subdaily + "\"\n")
                    mapfile.write("\t\t\"" + identifier + "_SUBDAILY\"\t\t" + subdailyParamRegex + "\n")
                    if wmsGroupName:
                        mapfile.write("\t\t\"default_" + wmsGroupName + "_SUBDAILY\"\t\"" + default_subdaily + "\"\n")
                        mapfile.write("\t\t\"" + wmsGroupName + "_SUBDAILY\"\t\t" + subdailyParamRegex + "\n")
                mapfile.write("\tEND\n")
                mapfile.write("\tMETADATA\n")
                mapfile.write("\t\t\"wms_title\"\t\t\"" + title + "\"\n")
                mapfile.write("\t\t\"wms_extent\"\t\t\"" + minx + " " + miny + " " + maxx + " " + maxy + "\"\n")
                if not static and len(detected_times) > 0:
                    defaultDate = ''
                    timeExtent  = ''
                    for detected_time in detected_times:
                        defaultDate = detected_time.strip().split('/')[1]
                        timeExtent = timeExtent + detected_time.strip() + ","
                    mapfile.write("\t\t\"wms_timeextent\"\t\"" + timeExtent.rstrip(',') + "\"\n")
                    mapfile.write("\t\t\"wms_timedefault\"\t\"" + defaultDate + "\"\n")

                if wmsLayerGroupName is not None:
                    if wmsGroupName is not None:
                        wmsLayerGroupName += "/" + wmsGroupName
                    mapfile.write("\t\t\"wms_layer_group\"\t\"" + wmsLayerGroupName + "\"\n")
                elif wmsGroupName is not None:
                    mapfile.write("\t\t\"wms_layer_group\"\t\t\"" + wmsGroupName + "\"\n")

                if legend and legendUrl_png_h_url:
                    mapfile.write("\t\t\"wms_style\"\t\t\t\t\"default\"\n")
                    mapfile.write("\t\t\"wms_style_default_legendurl_width\"\t\"" + str(legendUrl_png_h_width) + "\"\n")
                    mapfile.write("\t\t\"wms_style_default_legendurl_height\"\t\"" + str(legendUrl_png_h_height) + "\"\n")
                    mapfile.write("\t\t\"wms_style_default_legendurl_format\"\t\"image/png\"\n")
                    mapfile.write("\t\t\"wms_style_default_legendurl_href\"\t\"" + legendUrl_png_h_url + "\"\n")

                if vectorType:
                    mapfile.write('\t\t"wms_enable_request"\t\t"GetLegendGraphic"\n')
                    mapfile.write('\t\t"wfs_getfeature_formatlist"\t\t"geojson,csv"\n')
                    mapfile.write('\t\t"gml_include_items"\t\t"all"\n')

                mapfile.write("\tEND\n")
                datacon = "DATA"
                if vectorType:
                    # check if we have json files; if yes, use that extension, otherwise assume shapefiles
                    jsonsearch = archiveLocation + '/[0-9]*/*.json'
                    if len(glob.glob(jsonsearch)) == 0:
                        extension = ''
                    else:
                        extension = '.json'
                        mapfile.write("\tCONNECTIONTYPE OGR\n")
                        datacon = "CONNECTION"
                else:
                    extension = '.mrf'
                if not static and year:
                    if subdaily:
                        mapfile.write("\t"+datacon+"\t\"" + archiveLocation + "/" +
                                    yearDirPattern + "/" + fileNamePrefix +
                                    timeDirPattern + subdailyDirPattern +
                                    extension + "\"\n")
                    else:
                        mapfile.write("\t"+datacon+"\t\"" + archiveLocation + "/" +
                                    yearDirPattern + "/" + fileNamePrefix +
                                    timeDirPattern + extension + "\"\n")
                elif not static and not year:
                    mapfile.write("\t"+datacon+"\t\"" + archiveLocation + "/" +
                                fileNamePrefix + timeDirPattern + extension +
                                "\"\n")
                else:
                    mapfile.write("\t"+datacon+"\t\"" + archiveLocation + "/" +
                                fileNamePrefix + extension + "\"\n")
                mapfile.write("\tPROJECTION\n")
                mapfile.write('\t\t\"init={0}"\n'.format(projection.id.lower()))
                mapfile.write("\tEND\n")
                if vectorType and mapfileLayerContents:
                    try:
                        with open(mapfileLayerContents, 'r') as f:
                            mapfile.write(f.read())
                            mapfile.write("\n")
                    except:
                        log_sig_err(
                            "Couldn't read mapfile LAYER contents file: " +
                            mapfileLayerContents, sigevent_url)
                mapfile.write("END\n")

# Use config filename or directory for logging the current config outside of loop
if not options.layer_config_filename:
    current_conf = configuration_directory
else:
    current_conf = configuration_filename

# run scripts
if no_twms == False:
    for key, twms_endpoint in twms_endpoints.iteritems():
        #twms
        if twms_endpoint.cacheConfigBasename:
            print "\nRunning commands for endpoint: " + twms_endpoint.path
            cmd = depth + '/oe_create_cache_config -cbd ' + twms_endpoint.path + " " + twms_endpoint.path + '/' + twms_endpoint.cacheConfigBasename + '.config'
            run_command(cmd, sigevent_url)
            cmd = depth + '/oe_create_cache_config -cxd ' + twms_endpoint.path + " " + twms_endpoint.path + '/' + twms_endpoint.cacheConfigBasename + '.xml'
            run_command(cmd, sigevent_url)
        if no_cache == False:
            if twms_endpoint.cacheConfigLocation:
                print '\nCopying: ' + twms_endpoint.path + '/' + twms_endpoint.cacheConfigBasename + '.config' + ' -> ' + twms_endpoint.cacheConfigLocation + '/' + twms_endpoint.cacheConfigBasename + '.config'
                shutil.copyfile(
                    twms_endpoint.path + '/' +
                    twms_endpoint.cacheConfigBasename + '.config',
                    twms_endpoint.cacheConfigLocation + '/' +
                    twms_endpoint.cacheConfigBasename + '.config')
                print '\nCopying: ' + twms_endpoint.path + '/' + twms_endpoint.cacheConfigBasename + '.xml' + ' -> ' + twms_endpoint.cacheConfigLocation + '/' + twms_endpoint.cacheConfigBasename + '.xml'
                shutil.copyfile(
                    twms_endpoint.path + '/' +
                    twms_endpoint.cacheConfigBasename + '.xml',
                    twms_endpoint.cacheConfigLocation + '/' +
                    twms_endpoint.cacheConfigBasename + '.xml')
        if twms_endpoint.getCapabilities:
            # Add layer metadata to getCapabilities
            layer_xml = ""
            for xml_file in sorted(
                    os.listdir(twms_endpoint.path), key=lambda s: s.lower()):
                if xml_file.endswith("_gc.xml") and xml_file != "getCapabilities.xml":
                    layer_xml = layer_xml + open(twms_endpoint.path + '/' + str(xml_file), 'r').read()
            getCapabilities_file = twms_endpoint.path + '/getCapabilities.xml'
            getCapabilities_base = open(getCapabilities_file, 'r+')
            gc_lines = getCapabilities_base.readlines()
            for idx in range(0, len(gc_lines)):
                if "\t</Layer>" in gc_lines[idx]:
                    gc_lines[idx] = layer_xml + gc_lines[idx]
                    print '\nAdding layers to TWMS GetCapabilities'
                getCapabilities_base.seek(0)
                getCapabilities_base.truncate()
                getCapabilities_base.writelines(gc_lines)
            getCapabilities_base.close()
            if no_xml == False:
                if not os.path.exists(twms_endpoint.getCapabilities):
                    os.makedirs(twms_endpoint.getCapabilities)
                print '\nCopying: ' + twms_endpoint.path + '/getCapabilities.xml' + ' -> ' + twms_endpoint.getCapabilities + '/getCapabilities.xml'
                shutil.copyfile(
                    twms_endpoint.path + '/getCapabilities.xml',
                    twms_endpoint.getCapabilities + '/getCapabilities.xml')
        if twms_endpoint.getTileService:
            # Add layer metadata to getTileService
            layer_xml = ""
            for xml_file in sorted(os.listdir(twms_endpoint.path), key=lambda s: s.lower()):
                if xml_file.endswith("_gts.xml") and xml_file != "getTileService.xml":
                    layer_xml = layer_xml + open(twms_endpoint.path + '/' + str(xml_file), 'r').read()
            getTileService_file = twms_endpoint.path + '/getTileService.xml'
            getTileService_base = open(getTileService_file, 'r+')
            gc_lines = getTileService_base.readlines()
            for idx in range(0, len(gc_lines)):
                if "</TiledPatterns>" in gc_lines[idx]:
                    gc_lines[idx] = layer_xml + gc_lines[idx]
                    print '\nAdding layers to TWMS GetTileService'
                getTileService_base.seek(0)
                getTileService_base.truncate()
                getTileService_base.writelines(gc_lines)
            getTileService_base.close()
            if no_xml == False:
                if not os.path.exists(twms_endpoint.getTileService):
                    os.makedirs(twms_endpoint.getTileService)
                print '\nCopying: ' + twms_endpoint.path + '/getTileService.xml' + ' -> ' + twms_endpoint.getTileService + '/getTileService.xml'
                shutil.copyfile(
                    twms_endpoint.path + '/getTileService.xml',
                    twms_endpoint.getTileService + '/getTileService.xml')

if no_wmts == False:
    for key, wmts_endpoint in wmts_endpoints.iteritems():
        #wmts
        if wmts_endpoint.cacheConfigBasename:
            print "\nRunning commands for endpoint: " + wmts_endpoint.path
            cmd = depth + '/oe_create_cache_config -cbd ' + wmts_endpoint.path + " " + wmts_endpoint.path + '/' + wmts_endpoint.cacheConfigBasename + '.config'
            try:
                run_command(cmd, sigevent_url)
            except:
                log_sig_err("Error in generating binary cache config using command: " + cmd, sigevent_url)
            cmd = depth + '/oe_create_cache_config -cxd ' + wmts_endpoint.path + " " + wmts_endpoint.path + '/' + wmts_endpoint.cacheConfigBasename + '.xml'
            try:
                run_command(cmd, sigevent_url)
            except:
                log_sig_err(
                    "Error in generating XML cache config using command: " +
                    cmd, sigevent_url)
        if no_cache == False:
            if wmts_endpoint.cacheConfigLocation:
                print '\nCopying: ' + wmts_endpoint.path + '/' + wmts_endpoint.cacheConfigBasename + '.config' + ' -> ' + wmts_endpoint.cacheConfigLocation + '/' + wmts_endpoint.cacheConfigBasename + '.config'
                shutil.copyfile(
                    wmts_endpoint.path + '/' +
                    wmts_endpoint.cacheConfigBasename + '.config',
                    wmts_endpoint.cacheConfigLocation + '/' +
                    wmts_endpoint.cacheConfigBasename + '.config')
                print '\nCopying: ' + wmts_endpoint.path + '/' + wmts_endpoint.cacheConfigBasename + '.xml' + ' -> ' + wmts_endpoint.cacheConfigLocation + '/' + wmts_endpoint.cacheConfigBasename + '.xml'
                shutil.copyfile(
                    wmts_endpoint.path + '/' +
                    wmts_endpoint.cacheConfigBasename + '.xml',
                    wmts_endpoint.cacheConfigLocation + '/' +
                    wmts_endpoint.cacheConfigBasename + '.xml')
        if wmts_endpoint.getCapabilities:
            # Add layer metadata to getCapabilities
            layer_xml = ""
            for xml_file in sorted(
                    os.listdir(wmts_endpoint.path), key=lambda s: s.lower()):
                if xml_file.endswith(
                        ".xml") and xml_file != "getCapabilities.xml" and (
                            xml_file.startswith("cache") == False):
                    layer_xml = layer_xml + open(
                        wmts_endpoint.path + '/' + str(xml_file), 'r').read()
            getCapabilities_file = wmts_endpoint.path + '/getCapabilities.xml'
            try:
                getCapabilities_base = open(getCapabilities_file, 'r+')
                gc_lines = getCapabilities_base.readlines()
                for idx in range(0, len(gc_lines)):
                    if "<Contents>" in gc_lines[idx]:
                        gc_lines[idx] = gc_lines[idx] + layer_xml
                        print '\nAdding layers to WMTS GetCapabilities'
                    if "</Contents>" in gc_lines[
                            idx] and " </TileMatrixSet>" not in gc_lines[idx -
                                                                         1]:
                        gc_lines[
                            idx] = wmts_endpoint.projection.tilematrixset_xml[
                                2:] + '\n' + gc_lines[idx]
                        print "\nAdding TileMatrixSet to WMTS GetCapabilities"
                    getCapabilities_base.seek(0)
                    getCapabilities_base.truncate()
                    getCapabilities_base.writelines(gc_lines)
                getCapabilities_base.close()
            except:
                log_sig_err(
                    "Couldn't read GetCapabilities file: " +
                    getCapabilities_file, sigevent_url)
            if no_xml == False:
                print '\nCopying: ' + getCapabilities_file + ' -> ' + wmts_endpoint.getCapabilities + '/getCapabilities.xml'
                shutil.copyfile(
                    getCapabilities_file,
                    wmts_endpoint.getCapabilities + '/getCapabilities.xml')
                if not os.path.exists(wmts_endpoint.getCapabilities +
                                      '1.0.0/'):
                    os.makedirs(wmts_endpoint.getCapabilities + '1.0.0')
                print '\nCopying: ' + getCapabilities_file + ' -> ' + wmts_endpoint.getCapabilities + '/1.0.0/WMTSCapabilities.xml'
                shutil.copyfile(
                    getCapabilities_file, wmts_endpoint.getCapabilities +
                    '/1.0.0/WMTSCapabilities.xml')

# Create the consolidated mapfile based on the snippets created previously
if create_mapfile is True:
    for key, wms_endpoint in wms_endpoints.iteritems():
        if wms_endpoint.mapfileLocation is not None and wms_endpoint.mapfileStagingLocation is not None and wms_endpoint.mapfileConfigLocation is not None and wms_endpoint.mapfileConfigBasename is not None:
            # Create a new staging mapfile and add header, layers, and footer
            staging_mapfile = os.path.join(
                wms_endpoint.mapfileStagingLocation,
                wms_endpoint.mapfileLocationBasename)
            output_mapfile = os.path.join(
                wms_endpoint.mapfileLocation,
                wms_endpoint.mapfileLocationBasename + ".map")
            with open(staging_mapfile, 'w+') as mapfile:
                # Append header to mapfile if there is one
                mapfile_config_prefix = os.path.join(
                    wms_endpoint.mapfileConfigLocation,
                    wms_endpoint.mapfileConfigBasename)
                try:
                    with open(mapfile_config_prefix + '.header',
                              'r') as header:
                        mapfile.write(header.read())
                        print "\nUsing mapfile header: " + header.name
                except IOError:
                    pass
                # Iterate through layer mapfile snippets
                layers = [
                    os.path.join(wms_endpoint.mapfileStagingLocation, sfile)
                    for sfile in sorted(
                        os.listdir(wms_endpoint.mapfileStagingLocation),
                        key=unicode.lower) if sfile.endswith('.map') and
                    not sfile.startswith(wms_endpoint.mapfileLocationBasename)
                ]
                for layer in layers:
                    with open(layer, 'r') as f:
                        mapfile.write('\n')
                        mapfile.write(f.read())
                # Append footer to mapfile if there is one
                try:
                    with open(mapfile_config_prefix + '.footer',
                              'r') as footer:
                        mapfile.write('\n')
                        mapfile.write(footer.read())
                        print "\nUsing mapfile footer: " + footer.name
                except IOError:
                    mapfile.write('\nEND')
                    pass
            print '\nCopying: Mapfile {0} to {1}'.format(
                staging_mapfile, output_mapfile)
            shutil.copyfile(staging_mapfile, output_mapfile)
        else:
            if wms_endpoint.mapfileLocation is None:
                log_sig_err(
                    'Mapfile creation enabled but no <MapfileLocation> present in environment config file.',
                    sigevent_url)
            if wms_endpoint.mapfileStagingLocation is None:
                log_sig_err(
                    'Mapfile creation enabled but no <MapfileStagingLocation> present in environment config file.',
                    sigevent_url)
            if wms_endpoint.mapfileConfigLocation is None:
                log_sig_err(
                    'Mapfile creation enabled but no <MapfileConfigLocation> present in environment config file.',
                    sigevent_url)
            if wms_endpoint.mapfileConfigBasename is None:
                log_sig_err(
                    'Mapfile creation enabled but no "basename" attribute specified for <MapfileConfigLocation>.',
                    sigevent_url)

print '\n*** Layers have been configured successfully ***'
if no_cache == False:
    print '\nThe Apache server must be restarted to reload the cache configurations\n'

if restart == True:
    cmd = 'sudo apachectl stop'
    try:
        run_command(cmd, sigevent_url)
    except Exception, e:
        log_sig_err(str(e), sigevent_url)
    cmd = 'sleep 3'
    run_command(cmd, sigevent_url)
    cmd = 'sudo apachectl start'
    try:
        run_command(cmd, sigevent_url)
    except Exception, e:
        log_sig_err(str(e), sigevent_url)
    print '\nThe Apache server was restarted successfully'

completion = "The OnEarth Layer Configurator completed "
if len(warnings) > 0:
    message = completion + "with warnings."
    print "Warnings:"
    for warning in warnings:
        print warning
if len(errors) > 0:
    message = completion + "with errors."
    print "\nErrors:"
    for error in errors:
        print error
if len(warnings) == 0 and len(errors) == 0:
    message = completion + "successully."
print ""
message = message + " " + (
    "Cache configurations created.",
    "Cache configurations staged.")[no_cache] + " " + (
        "Server XML created", "Server XML staged")[no_xml] + "." + " " + (
            "Apache not restarted",
            "Apache restarted")[restart] + "." + " " + (
                "Legends not generated",
                "Legends generated")[legend] + "." + " " + (
                    "Archive links not generated",
                    "Archive links generated")[links] + ". " + (
                        "Mapfiles not configured", "Mapfiles configured"
                    )[create_mapfile] + "." + " Warnings: " + str(
                        len(warnings)) + ". Errors: " + str(len(errors)) + "."

try:
    log_info_mssg(asctime() + " " + message)
    sigevent('INFO', asctime() + " " + message, sigevent_url)
except urllib2.URLError:
    None
log_info_mssg('Exiting oe_configure_layer.')

if len(errors) > 0:
    sys.exit(len(errors))
else:
    sys.exit(0)
