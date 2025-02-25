# OnEarth Remote Layers

OnEarth is able to add layers from a remote GetCapabilities file to its own GetCapabilities file to form a "combined" GetCapabilities that contains layers from both sources (same feature applies for GetTileService). This is useful if layers are being served from multiple sources, but a single endpoint is desired to present those layers to client applications.


# How It Works
The `oe_configure_remote_layers.py` script is used to read a special layer configuration file (described in the next section) which will read a remote GetCapabilities or GetTileService file. For all of the layers found in the remote GetCapabilties file that match (or don't match) provided layer inclusion and exclusion filters in the layer configuration file, a layer XML file will be created in the staging directories as defined in the environment configuration file. When OnEarth goes to configure its layers, it will incorporate those layer XML files like it would any regular layer.


# Configuration

### Layer Config File
Remote layers require a differently-structured XML file from the normal OnEarth layer config. 

**Note that with remote layers, the `oe_configure_layer` tool actually configures all the remote layers from an external endpoint at once, instead of individually. You'll only need one configuration file per external endpoint.**

The remote layers tool scrapes data from the GetCapabilities file of the specified endpoint, then configures the layers using data from that file. Here's an example:

```
<RemoteGetCapabilities>
	<SrcWMTSGetCapabilitiesURI>https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/1.0.0/WMTSCapabilities.xml</SrcWMTSGetCapabilitiesURI>
	<SrcTWMSGetCapabilitiesURI>https://gibs.earthdata.nasa.gov/twms/epsg4326/best/twms.cgi?request=GetCapabilities</SrcTWMSGetCapabilitiesURI>
	<SrcTWMSGetTileServiceURI>https://gibs.earthdata.nasa.gov/twms/epsg4326/best/twms.cgi?request=GetTileService</SrcTWMSGetTileServiceURI>
	<SrcLocationRewrite internal="https://gibs.earthdata.nasa.gov" external="https://gitc.earthdata.nasa.gov" />
	<EnvironmentConfig>/etc/onearth/config/conf/environment_geographic.xml</EnvironmentConfig>
    <IncludeLayer>Landsat_WELD_CorrectedReflectance_TrueColor_Global_Annual</IncludeLayer>
	<ExcludeLayer>BlueMarble_NextGeneration</ExcludeLayer>
</RemoteGetCapabilities>
```

#### Remote Config Elements:

`<SrcWMTSGetCapabilitiesURI>` -- Specifies the WMTS GetCapabilities file that `oe_configure_remote_layers.py` will use to build the layer configs, including source URL info.

`<SrcTWMSGetCapabilitiesURI>` -- Specifies the TWMS GetCapabilities file that `oe_configure_remote_layers.py` will use to build the layer configs, including source URL info.

`<SrcTWMSGetTileServiceURI>` -- Specifies the TWMS GetCapabilities file that `oe_configure_remote_layers.py` will use to build the layer configs, including source URL info.

-----

`<SrcLocationRewrite>` (optional) -- This specifies whether we should replace the external source URI with an internal one.

##### Attributes:

`internal` -- The internal URI that will replace the external URI.

`external`  -- The external URI that will be replaced by the internal URI.

-------

`<EnvironmentConfig>` (required) -- The environment configuration file to be used in setting up this endpoint.

`<IncludeLayer>` (optional, can be multiple) -- Include one of these elements for each layer from the source endpoint that you want explicitly included. If at least one of these elements is included, the layer tool will skip all layers found in the source GetCapabilities _not_ specified in an `<IncludeLayer>` element.

`<ExcludeLayer>` (optional, can be multiple) -- Include one of these elements for each layer from the source endpoint that you don't want included. The layer tool will skip any layers in the source GetCapabilities it finds with this name.
