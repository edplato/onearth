#!/bin/bash

# Copyright (c) 2002-2015, California Institute of Technology.
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

ct() {
  while read a
  do
    echo $a
  done
}

QS=${QUERY_STRING%%GetMap*}
if [[ -z ${QS##*=} ]]
then
  if [[ $QUERY_STRING == *jpeg* ]]
  then
    echo -e "Content-type: image/jpeg\n"
    cat black.jpg
  elif [[ $QUERY_STRING == *vnd.mapbox-vector-tile* ]]
  then
  	echo -e "Content-type: application/vnd.mapbox-vector-tile\n"
  else
    echo -e "Content-type: image/png\n"
    cat transparent.png
  fi
  exit
else
  # GetCapabilities is only here for WorldWind
  if [[ $QUERY_STRING == *GetCapabilities* ]]
  then
    echo -e "Content-type: text/xml\n"
    cat .lib/getCapabilities.xml
    exit
  else
    # Don't believe this works as the file is located in .lib
    # Believe it is served from there by the Apache module
    if [[ $QUERY_STRING == *GetTileService* ]]
    then
      echo -e "Content-type: text/xml\n"
      cat getTileService.xml
      exit
    fi
  fi
  echo -e "Content-type: text/html\n"
  echo "<body>This is not a full WMS server!</body>"
fi
