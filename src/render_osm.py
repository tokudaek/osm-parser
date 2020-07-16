#!/usr/bin/env python3
"""Parse OSM data
"""
import matplotlib
#matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.colors as colors
from matplotlib.colors import LogNorm
from matplotlib.collections import LineCollection
from matplotlib.collections import PatchCollection
from matplotlib.patches import Polygon
plt.style.use('seaborn')

##########################################################
import fiona
from shapely import geometry
from descartes.patch import PolygonPatch#, PathPatch
from matplotlib import cm
from matplotlib.colors import LinearSegmentedColormap
import geojson
##########################################################

import os
import numpy as np
import numpy
import numpy.linalg
from numpy.linalg import norm as norm
import argparse
import xml.etree.ElementTree as ET
from rtree import index
import logging
from logging import debug, warning
import random
import time
import pickle
import pandas as pd

import scipy.spatial
from pyproj import Proj, transform

import collections

########################################################## DEFS
MAX_ARRAY_SZ = 1048576  # 2^20

intersection_delta = 1e-4
VARS = ['nodes', 'segments', 'crossings', 'artpoints', 'crossing_points',
        'regions', 'segcounts', 'intersection_counts']

WAY_TYPES = ['motorway', 'trunk', 'primary', 'secondary', 'tertiary',
             'unclassified', 'residential', 'service', 'living_street']
ROI = {
    'road': {'k': 'highway', 'v': WAY_TYPES, 'c': 'lightgray', 'z': 0},
    'building': {'k': 'building', 'v': ['yes'], 'c': 'gray', 'z': 0},
    'water': {'k': 'landuse', 'v': ['reservoir'], 'c': 'skyblue', 'z': 2},
    'park': {'k': 'leisure', 'v': ['park'], 'c': 'lightgreen', 'z': 1},
       }
    # 'hospital': {'k': 'amenity', 'v': ['hospital'], 'c': 'red', 'z': 2},
##########################################################

def plot_count_pictures(outdir):
    import mpl_scatter_density

    from astropy.visualization import LogStretch
    from astropy.visualization.mpl_normalize import ImageNormalize
    norm = ImageNormalize(vmin=0., vmax=17500, stretch=LogStretch())

    df = pd.read_csv('/mnt/3tb-home/frodo/projects/osm-parser/data/20180901_peds_manhattan_workdays_crs4326.csv')
    x = df['lon']
    y = df['lat']
    dpi = 150 # 72, 96,150

    fig = plt.figure(figsize=(6,8))
    ax = fig.add_subplot(1, 1, 1, projection='scatter_density')
    density = ax.scatter_density(x, y, norm=norm, cmap='Greys')
    # ax.set_xlim(-5, 10)
    # ax.set_ylim(-5, 10)
    ax.axis('off')
    ax.set_facecolor('white')
    plt.tick_params(axis='both', which='both', bottom=False,
            top=False, left=False, labelbottom=False) 

    from mpl_toolkits.axes_grid1 import make_axes_locatable
    axcb = fig.colorbar(density, label='Number of pictures', ticks=[0,4000,8000,16000],
                        aspect=70,
                        cax = fig.add_axes([.85, .2, 0.01, 0.3])
                        )

    cbarfontsize = 12
    text = axcb.ax.yaxis.label
    font = matplotlib.font_manager.FontProperties(size=cbarfontsize)
    text.set_font_properties(font)
    axcb.ax.tick_params(labelsize=cbarfontsize)
    axcb.formatter.set_scientific(True)
    axcb.formatter.set_powerlimits((0,4))
    axcb.formatter._useMathText = True
    axcb.ax.yaxis.get_offset_text().set_fontsize(cbarfontsize)
    axcb.update_ticks()
    fig.savefig(os.path.join(outdir, 'countpictures.pdf'))

##########################################################
# mapbox_back = '#1a1a1a'
# matplotlib_plot_area = '#363633'
# mapbox_plot_area = '#3a3a3a'
def plot_map(ax, source):
    norm = colors.Normalize(vmin=1, vmax=2*len(source))
    sm = cm.ScalarMappable(norm, cmap=cm.Paired)
    alpha, cnt = .8, 1
    for r in source:
        # edge_color, color = sm.to_rgba(cnt), sm.to_rgba(cnt+1)
        edge_color = 'lightgray'
        # color = '#e5e5e5'
        # color = '#363633'
        color = 'lightgray'
        cnt += 2
        shape = geometry.shape(r["geometry"])
        if isinstance(shape, geometry.Polygon):
            poly = shape
            ax.add_patch(PolygonPatch(poly, fc=color, ec=edge_color, alpha=alpha, zorder=1))
        elif isinstance(shape, geometry.MultiPolygon):
            for poly in shape:
                ax.add_patch(PolygonPatch(poly, fc=color, ec=edge_color, alpha=alpha, zorder=1))
        elif isinstance(shape, geometry.LineString):
            ax.plot(*shape.xy, color='gray', linewidth=3, solid_capstyle='round', zorder=1)
        elif isinstance(shape, geometry.MultiLineString):
            for line in shape:
                ax.plot(*line.xy, color='gray', linewidth=3, solid_capstyle='round', zorder=1)
        else:
            print(r["geometry"]["type"])
            raise Exception("?")


##########################################################
def compute_or_load(inputosm, countcsv, outdir):
    d = {}

    if os.path.exists(outdir):
        debug('Output path {} exists. Loading files...'.format(outdir))
        for var in VARS:
            with open(os.path.join(outdir, var + '.pkl'), 'rb') as fh:
                d[var] = pickle.load(fh)
        debug('Successfully load files from {}'.format(outdir))
    else:
        os.mkdir(outdir)
        tree = ET.parse(inputosm)
        root = tree.getroot() # Tag osm
        ways, invways, d['regions'] = parse_ways(root)
        d['nodes'] = parse_nodes(root)
        ways, invways = filter_out_orphan_nodes(ways, invways, d['nodes'])
        d['crossings'] = get_crossings(invways)
        d['segments'], invsegments = get_segments(ways, d['crossings'])
        d['artpoints'] = evenly_space_segments(d['segments'], d['nodes'])
        d['crossing_points'] = get_crossing_points_numpy(d['crossings'], d['nodes'])
        s, i = get_count_by_segment(countcsv, d['segments'], d['artpoints'],
                                    d['crossing_points'])
        d['segcounts'], d['intersection_counts'] = s, i

        for v in VARS:
            fh = open(os.path.join(outdir, v + '.pkl'), 'wb')
            pickle.dump(d[v], fh)
            fh.close()
    return d

##########################################################
def parse_ways(root):
    """Get all ways in the xml struct. We are interested in two types of ways: streets
    and regions of interest

    Args:
    root(ET): root element

    Returns:
    dict of list: hash of wayid as key and list of nodes as values;
    dict of list: hash of nodeid as key and list of wayids as values;
    dict of dict of list: hash of regiontype as 
    """

    t0 = time.time()
    ways = {}
    invways = {}
    regions = {r: {} for r in ROI.keys()}

    for way in root:
        if way.tag != 'way': continue

        wayid = int(way.attrib['id'])
        isstreet = False
        regiontype = None

        nodes = []
        for child in way:
            if child.tag == 'nd':
                nodes.append(int(child.attrib['ref']))
            elif child.tag == 'tag':
                for k, r in ROI.items():
                    if child.attrib['k'] == r['k'] and child.attrib['v'] in r['v']:
                        regiontype = k
                        break

        if regiontype is None: continue

        if regiontype == 'road':
            ways[wayid] = nodes
            for node in nodes: # Create inverted index of ways
                if node in invways.keys(): invways[node].append(wayid)
                else: invways[node] = [wayid]
        else:
            regions[regiontype][wayid] = nodes  # Regions of interest

    debug('Found {} ways ({:.3f}s)'.format(len(ways.keys()), time.time() - t0))
    return ways, invways, regions

##########################################################
def parse_nodes(root):#, invways):
    """Get all nodes in the xml struct

    Args:
    root(ET): root element

    Returns:
    dict: id as keys and (int, int) as values
    """
    t0 = time.time()
    nodes = {}

    for child in root:
        if child.tag != 'node': continue # not node
        att = child.attrib
        nodes[int(att['id'])] = (float(att['lat']), float(att['lon']))
    debug('Found {} (traversable) nodes ({:.3f}s)'.format(len(nodes.keys()),
                                                          time.time() - t0))
    return nodes

##########################################################
def get_crossings(invways):
    """Get crossings

    Args:
    invways(dict of list): inverted list of the ways. It s a dict of nodeid as key and
    list of wayids as values

    Returns:
    set: set of crossings
    """

    crossings = set()
    for nodeid, waysids in invways.items():
        if len(waysids) > 1:
            crossings.add(nodeid)
    return crossings

##########################################################
def filter_out_orphan_nodes(ways, invways, nodes):
    """Check consistency of nodes in invways and nodes and fix them in case
    of inconsistency
    It can just be explained by the *non* exitance of nodes, even though they are
    referenced inside ways (<nd ref>)

    Args:
    invways(dict of list): nodeid as key and a list of wayids as values
    nodes(dict of 2-uple): nodeid as key and (x, y) as value
    ways(dict of list): wayid as key and an ordered list of nodeids as values

    Returns:
    dict of list, dict of lists
    """

    ninvways = len(invways.keys())
    nnodes = len(nodes.keys())
    if ninvways == nnodes: return ways, invways

    validnodes = set(nodes.keys())

    # Filter ways
    for wayid, nodes in ways.items():
        newlist = [ nodeid for nodeid in nodes if nodeid in validnodes ]
        ways[wayid] = newlist

    # Filter invways
    invwaysnodes = set(invways.keys())
    invalid = invwaysnodes.difference(validnodes)

    for nodeid in invalid:
        del invways[nodeid]

    ninvways = len(invways.keys())
    debug('Filtered {} orphan nodes.'.format(ninvways - nnodes))
    return ways, invways

##########################################################
def get_segments(ways, crossings):
    """Get segments, given the ways and the crossings

    Args:
    ways(dict of list): hash of wayid as key and list of nodes as values
    crossings(set): set of nodeids in crossings

    Returns:
    dict of list: hash of segmentid as key and list of nodes as value
    dict of list: hash of nodeid as key and list of sids as value
    """

    t0 = time.time()
    segments = {}
    invsegments = {}

    sid = 0 # segmentid
    segment = []
    for w, nodes in ways.items():
        if not crossings.intersection(set(nodes)):
            segments[sid] = nodes
            sid += 1
            continue

        segment = []
        for node in nodes:
            segment.append(node)

            if node in crossings and len(segment) > 1:
                segments[sid] = segment
                for snode in segment:
                    if snode in invsegments: invsegments[snode].append(sid)
                    else: invsegments[snode] = [sid]
                segment = [node]
                sid += 1

        segments[sid] = segment # Last segment
        for snode in segment:
            if snode in invsegments: invsegments[snode].append(sid)
            else: invsegments[snode] = [sid]
        sid += 1

    debug('Found {} segments ({:.3f}s)'.format(len(segments.keys()), time.time() - t0))
    return segments, invsegments

##########################################################
def evenly_space_segment(segment, nodes, epsilon):
    """Evenly space one segment

    Args:
    segment(list): nodeids composing the segment
    nodes(dict of list): hash with nodeid as value and 2-uple as value

    Returns:
    coords(ndarray(., 2)): array of coordinates
    """
    #debug(segment)
    prevnode = np.array(nodes[segment[0]])
    points = [prevnode]

    for nodeid in segment[1:]:
        node = np.array(nodes[nodeid])
        d = norm(node - prevnode)
        if d < epsilon:
            points.append(node)
            prevnode = node
            continue

        nnewnodes = int(d / epsilon)
        direc = node - prevnode
        direc = direc / norm(direc)

        cur = prevnode
        for i in range(nnewnodes):
            newnode = cur + direc*epsilon
            points.append(newnode)
            cur = newnode
        prevnode = node
    return np.array(points)



##########################################################
def evenly_space_segments(segments, nodes, epsilon=0.0001):
    """Evenly space all segments and create artificial points

    Args:
    segments(dict of list): hash of segmentid as key and list of nodes as values
    nodes(dict of list): hash with nodeid as value and 2-uple as value

    Returns:
    points(ndarray(., 3)): rows represents points and first and second columns
    represent coordinates and third represents the segmentid
    """

    t0 = time.time()
    points = np.ndarray((MAX_ARRAY_SZ, 3))
    idx = 0
    for sid, segment in segments.items():
        coords = evenly_space_segment(segment, nodes, epsilon)
        n, _ = coords.shape
        points[idx:idx+n, 0:2] = coords
        points[idx:idx+n, 2] = sid
        idx = idx + n
    debug('New {} support points ({:.3f}s)'.format(idx, time.time() - t0))
    return points[:idx, :]

###########################################################
def get_crossing_points_numpy(crossings, nodes):
    locations = []

    for nid in crossings:
        locations.append(np.array(nodes[nid]))

    return np.array(locations)

##########################################################
def get_count_by_segment(csvinput, segments, artpoints, crossing_points):
    t0 = time.time()
    artpointstree = scipy.spatial.cKDTree(artpoints[:,:2])
    crossing_pointstree = scipy.spatial.cKDTree(crossing_points[:,:2])

    debug('cKDTree created ({:.3f}s)'.format(time.time() - t0))

    t0 = time.time()
    fh = open(csvinput)
    fh.readline() # Header

    #imageid,n,x,y,t
    #denom = np.ones(nsegments)

    nerrors = 0
    maxcount = 0

    querycoords = []
    local_counts = []

    lats = []
    longs = []

    for i, line in enumerate(fh):
        arr = line.split(',')
        count = int(arr[1])
        # count = 1
        if not arr[2]:
            nerrors += 1
            continue
        longs.append(float(arr[2]))
        lats.append(float(arr[3]))
        local_counts.append(count)

    longs = np.array(longs)
    lats = np.array(lats)

    if count > maxcount:
        maxcount = count

    querycoords = np.hstack( (lats[:,None],longs[:,None]) ) #.append( (lat, lon) )
    _,artids = artpointstree.query(querycoords, k=1, n_jobs=-1)
    crossing_distance,corssing_index = crossing_pointstree.query(querycoords, k=1, n_jobs=-1)
    sids = artpoints[artids,2]

    nsegments = len(segments.keys())
    counts = np.zeros(nsegments)
    denom = np.zeros(nsegments)

    nintersections = crossing_points.shape[0]
    intersection_counts = np.zeros(nintersections)
    intersection_denom = np.zeros(nintersections)

    for idx in range(querycoords.shape[0]):
        if crossing_distance[idx] < intersection_delta:
            intersection_counts[int(corssing_index[idx])] += local_counts[idx]
            intersection_denom[int(corssing_index[idx])] += 1
        else:
            counts[int(sids[idx])] += local_counts[idx]
            denom[int(sids[idx])] += 1

    for i in range(nsegments):
        if denom[i] > 0:
            counts[i] /= denom[i]

    for i in range(nintersections):
        if intersection_denom[i] > 0:
            intersection_counts[i] /= intersection_denom[i]

    debug('Max count:{}'.format(maxcount))
    warning('nerrors:{}'.format(nerrors))
    fh.close()
    debug('Points aggregated ({:.3f}s)'.format(time.time() - t0))
    return counts, intersection_counts

##########################################################
def extradata_to_patches(nodes, extradataarray,rot,**kwargs):
    all_patches = []

    for bid,building in extradataarray.items():
        is_good=True
        locations = np.zeros((len(building),2),dtype=np.float)
        for idx,nid in enumerate(building):
            loc = nodes.get(nid,None)
            if loc is None:
                is_good = False
                break
            locations[idx,:] = nodes[nid][::-1]

        if is_good:
            locations = np.dot(locations,rot)
            all_patches.append(Polygon(locations, closed=True, fill=True,**kwargs))

    return all_patches

##########################################################
def render_map(d, render_all, outdir, logplot=False, winsize=(4.5, 16), angle=0,
               xlim=None, ylim=None):
    t0 = time.time()
    debug('Start rendering')

    fig, ax = plt.subplots(1,1, figsize=winsize)

    # Render segments
    i = 0
    maxvalue = np.log(25)

    rot = np.array( [[np.cos(angle),np.sin(angle)],[-np.sin(angle),np.cos(angle)]])

    lines = []
    values = []
    min_lats = []
    max_lats = []
    min_lons = []
    max_lons = []

    for wid, wnodes in d['segments'].items():
        i += 1

        lats = []; lons = []
        for nodeid in wnodes:
            a, o = d['nodes'][nodeid]
            lats.append(a)
            lons.append(o)

        rotated_vals = np.dot(np.column_stack([lons, lats]), rot)

        lines.append(rotated_vals)
        values.append(d['segcounts'][wid])
        min_lats.append(np.min(rotated_vals[:,1]))
        max_lats.append(np.max(rotated_vals[:,1]))
        min_lons.append(np.min(rotated_vals[:,0]))
        max_lons.append(np.max(rotated_vals[:,0]))

    values = np.array(values)
    min_lats = np.array(min_lats)
    max_lats = np.array(max_lats)
    min_lons = np.array(min_lons)
    max_lons = np.array(max_lons)

    if xlim is None:
        min_ax = min( np.min(l[:,0]) for l in lines  )
        max_ax = max( np.max(l[:,0]) for l in lines  )
    else:
        min_ax = xlim[0]
        max_ax = xlim[1]

    if ylim is None:
        min_ay = min( np.min(l[:,1]) for l in lines  )
        max_ay = max( np.max(l[:,1]) for l in lines  )
    else:
        min_ay = ylim[0]
        max_ay = ylim[1]

    values_to_measure = np.logical_and(values>0 ,
                            np.logical_and(
                                np.logical_and(min_ay <= max_lats,max_ay >= min_lats),
                                np.logical_and(min_ax <= max_lons,max_ax >= min_lons)))

    ## Render crossings
    #crossingscoords = np.ndarray((len(crossings), 2))
    #for j, crossing in enumerate(crossings):
    #    crossingscoords[j, :] = np.array(nodes[crossing])
    ##plt.scatter(crossingscoords[:, 1], crossingscoords[:, 0], c='black')

    low_percentile = np.percentile(values[values_to_measure], 1.0)
    high_percentile = np.percentile(values[values_to_measure], 99.0)

    if logplot:
        cnorm = colors.LogNorm(low_percentile,high_percentile)
    else:
        cnorm = colors.Normalize(low_percentile,high_percentile)

    # ax.set_facecolor('xkcd:salmon')
    # cmap = plt.get_cmap('Blues')
    # cmap.set_under(cmap(0))
    # cmap.set_bad(cmap(0))
    # cmap.set_over(cmap(1))
    # cmap = 'Greys'

    # mycolors = np.array([[26.0, 26, 26, 256],
    mycolors = np.array([[58, 58, 58, 256],
                       [76,114,176, 256]])/256

    n_bin = 30
    cmap_name = 'mapbox_grays'
    cmap = LinearSegmentedColormap.from_list(
                    cmap_name, mycolors, N=n_bin)
    # cmap = 'Greys'
    # cmap = 'Blues'
    cmap = 'PuBu'
    # cmap = 'OrRd'

    line_segments = LineCollection(lines,
                                   linewidths=(3),
                                   linestyles='solid',
                                   norm=cnorm,
                                   cmap=cmap,
                                   # colors=['blue'],
                                   capstyle='round')
    line_segments.set_array(values)
    ax.add_collection(line_segments)
    # axcb = fig.colorbar(line_segments,aspect=50,orientation='vertical')
    # axcb.set_label('Relative Pedestrian Density',size=15)

    crossing_points_rot = np.dot(d['crossing_points'], rot.T)
    # plt.scatter(crossing_points_rot[:,1],crossing_points_rot[:,0],s=16,
                # c=d['intersection_counts'],cmap=cmap,norm=cnorm)

    if render_all:
        all_patches = []
        for k, v in ROI.items():
            if k == 'road': continue # it is rendered in another part
            all_patches += extradata_to_patches(d['nodes'], d['regions'][k],
                                                rot, color=v['c'], zorder=v['z'])

        if len(all_patches) > 0 :
            p = PatchCollection(all_patches,match_original=True)
            ax.add_collection(p)

    plt.tight_layout()

    ax.set_xlim(min_ax, max_ax)
    ax.set_ylim(min_ay, max_ay)

    # source_shapefile = "/tmp/manhattanmap/wv.shp"
    source_shapefile = "data/vectormaps/manhattan.shp"
    # source_shapefile = "data/vectormaps/wv.shp"
    with fiona.open(source_shapefile) as source:
        plot_map(ax, source)

    axcb = fig.colorbar(line_segments,aspect=50,orientation='vertical')
    # ticks = np.linspace(0, 2, 50)
    # axcb.set_ticks(ticks)
    axcb.ax.tick_params(labelsize=40)
    axcb.set_label('Relative Pedestrian Density (log)',size=40)

    # ax.axis('off')
    plt.tick_params(
            axis='both',          # changes apply to the x-axis
            which='both',      # both major and minor ticks are affected
            bottom=False,      # ticks along the bottom edge are off
            top=False,         # ticks along the top edge are off
            left=False,
            labelbottom=False) # labels along the bottom edge are off

    plt.savefig(os.path.join(outdir, 'map.pdf'))
    debug('Finished exporting to image ({:.3f}s)'.format(time.time() - t0))

def export_geojson(picklesdir):
    """Export data to geojson

    Args:
    picklesdir(str): path to the pickles
    """
    nodes = pkl.load(open(pjoin(picklesdir, 'nodes.pkl'), 'rb'))
    segments = pkl.load(open(pjoin(picklesdir, 'segments.pkl'), 'rb'))
    segcounts = pkl.load(open(pjoin(picklesdir, 'segcounts.pkl'), 'rb'))

    features = []
    for segid, nodeids in segments.items():
        nodecoords = []
        for nodeid in nodeids:
            nodecoords.append(nodes[nodeid])
        linestring = geojson.LineString(nodecoords)
        features.append(geojson.Feature(geometry=linestring, properties={'avgcount':segcounts[segid]}))

    featurecollection = geojson.FeatureCollection(features)
    fh = open('/tmp/out.geojson', 'w')
    fh.write(geojson.dumps(featurecollection))
    fh.close()

##########################################################
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputosm', help='Input osm file')
    parser.add_argument('countcsv', help='Csv containing the count per segment')
    parser.add_argument('outdir', help='Output folder')
    parser.add_argument('--show', help='Show plot (requires display)', action='store_true')

    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG)

    xlim = None # (-83.920689662521823, -83.914650455250239)
    ylim = None # (-9.7181982288883919, -9.7147186948317152)
    # xlim = (-4756.8911953312218, -4754.7348445911539)
    # ylim = (592, 594.95)
    renderall = False
    # logscale = False
    logscale = True
    winsize = (32,32)
    # winsize = (16,16)
    # rotangle = 0.62
    rotangle = 0
    # rotangle = np.deg2rad(28.912)


    ########################################################## plot count
    df = pd.read_csv(args.countcsv)
    x = df.lon
    y = df.lat

    fig, ax = plt.subplots(1,1, figsize=(32,32))

    # heatmap, xedges, yedges = np.histogram2d(x, y, bins=200)
    # extent = [xedges[0], xedges[-1], yedges[0], yedges[-1]]
    # ax.imshow(heatmap.T, extent=extent, origin='lower', norm=LogNorm(),
              # cmap='Greys')
    # h = ax.hist2d(x, y, bins=200, cmap='Greys', norm=LogNorm())


    ##########################################################
    # h, xedges, yedges = np.histogram2d(x, y, bins=200)
    # pc = ax.pcolormesh(xedges, yedges, h.T, cmap='Greys', norm=LogNorm(),
                       # edgecolor=(0.5, 0.5, 0.5, 0.5), linewidth=0.0015625)
    # ax.set_xlim(xedges[0], xedges[-1])
    # ax.set_ylim(yedges[0], yedges[-1])

    ##########################################################
    # ax.tick_params(
        # axis='both',
        # which='both',
        # bottom=False,
        # top=False,
        # left=False,
        # right=False,
        # labelbottom=False,
        # labelleft=False) # labels along the bottom edge are off
    # ax.axis('off')
    # ax.grid(False)
    # axcb = fig.colorbar(pc, ax=ax)
    # axcb.set_label('Number of pictures',size=40)
    # axcb.ax.tick_params(labelsize=40)
    # # ax.set_axisbelow(True)
    # plt.savefig(os.path.join(args.outdir, 'count.pdf'))
    ##########################################################
    # input('waiting for users input')
    # plot_count_pictures(args.outdir)
    v = compute_or_load(args.inputosm, args.countcsv, args.outdir)
    render_map(v, renderall, args.outdir, logscale, winsize, rotangle, xlim, ylim)