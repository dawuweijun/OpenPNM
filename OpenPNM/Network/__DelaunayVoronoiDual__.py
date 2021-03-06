"""
===============================================================================
DelaunayVoronoiDual: Generate a random network with complementary Delaunay and
Voronoi networks, including connectings between them
===============================================================================

"""
from OpenPNM.Network import tools
import scipy as sp
import scipy.spatial as sptl
from OpenPNM.Network import GenericNetwork
from OpenPNM.Base import logging
logger = logging.getLogger(__name__)


class DelaunayVoronoiDual(GenericNetwork):
    r"""
    A dual network based on complementary Voronoi and Delaunay networks.  A
    Delaunay tessellation or triangulation is performed on randomly distributed
    base points, then the corresponding Voronoi diagram is generated.  Finally,
    each Delaunay nodes is connected to it's neighboring Voronoi vertices to
    create interaction between the two networks.

    All pores and throats are labelled according to their network (i.e.
    'pore.delaunay'), so they can be each assigned to a different Geometry.

    The dual-nature of this network is meant for modeling transport in the void
    and solid space simultaneously by treating one network (i.e. Delaunay) as
    voids and the other (i.e. Voronoi) as solid.  Interation such as heat
    transfer between the solid and void can be accomplished via the
    interconnections between the Delaunay and Voronoi nodes.

    Parameters
    ----------
    num_points : integer
        The number of random base points to distribute inside the domain.
        These points will become connected by the Delaunay triangulation.  The
        points will be generated by calling ``generate_base_points`` in
        Network.tools.

    points : array_like (num_points x 3)
        A list of coordinates for pre-generated points, typically produced
        using ``generate_base_points`` in Network.tools.  Note that base points
        should extend beyond the ``domain_size`` so that degenerate Voronoi
        points can be trimmed.

    domain_size : array_like
        The size and shape of the domain using for generating and trimming
        excess points. The argument is treated as follows:

        **sphere** : If a scalar or single element list is received, it's
        treated as the radius [r] of a sphere centered on [0, 0, 0].

        **cylinder** : If a two-element list is received it's treated as
        the radius and height of a cylinder [r, z] whose central axis
        starts at [0, 0, 0] and extends in the positive z-direction.

        **rectangle** : If a three element list is received, it's treated
        as the outer corner of rectangle [x, y, z] whose opposite corner
        lies at [0, 0, 0].

        By default, a domain size of [1, 1, 1] is used.

    trim_domain : Boolean
        If true (default) all nodes outside the given ``domain_size`` are
        removed, along with all their throats.  Setting this argument to False
        will skip this removal if an alternative manual trimming is preferred.

    Examples
    --------
    Points will be automatically generated if none are given:

    >>> import OpenPNM as op
    >>> net = op.Network.DelaunayVoronoiDual(num_points=50)

    The resulting network can be quickly visualized with
    ``op.Network.tools.plot_connections(net)``.  This plotting function also
    supports showing limited sets of throats for more clear inspectionk such as
    ``op.Network.tools.plot_connections(net, throats=net.throats('surface'))``.
    See its documentation for details.

    The default shape is a unit cube, but it's also possible to generate
    cylinders and spheres by specifying the domain size as [r, z] or [r],
    respectively:

    >>> sph = op.Network.DelaunayVoronoiDual(num_points=50, domain_size=[1])
    >>> cyl = op.Network.DelaunayVoronoiDual(num_points=50, domain_size=[1, 1])

    More control over the distribution of base points can be achieved by
    calling ``Network.tools.generate_base_points`` directly:

    >>> pts = op.Network.tools.generate_base_points(num_points=50,
    ...                                             domain_size=[1, 5])
    >>> pts -= [0, 0, 1]  # Shift points in the negative z-direction
    >>> cyl = op.Network.DelaunayVoronoiDual(points=pts, domain_size=[1, 3])

    All points lying below the z=0 plane and above the z=3 plane are trimmed,
    which gives the network *rough* ends since the points near the plane of
    reflection are all trimmed.

    """

    def __init__(self, num_points=None, points=None, domain_size=[1, 1, 1],
                 trim_domain=True, **kwargs):
        super().__init__(**kwargs)

        if points is None:
            if num_points is None:
                raise Exception('Must specify either "points" or "num_points"')
            points = tools.generate_base_points(num_points=num_points,
                                                domain_size=domain_size)

        # Perform tessellation
        vor = sptl.Voronoi(points=points)

        # Combine points
        pts_vor = vor.vertices
        pts_all = sp.vstack((points, pts_vor))
        Npts = sp.size(points, 0)
        Nvor = sp.size(pts_vor, 0)
        Nall = Nvor + Npts

        # Create adjacency matrix in lil format for quick matrix construction
        am = sp.sparse.lil_matrix((Nall, Nall))
        for ridge in vor.ridge_dict.keys():
            # Make Delaunay-to-Delauny connections
            [am.rows[i].extend([ridge[0], ridge[1]]) for i in ridge]
            row = vor.ridge_dict[ridge]
            if -1 not in row:
                # Index Voronoi vertex numbers by Npts
                row = [i + Npts for i in row]
                # Make Voronoi-to-Delaunay connections
                [am.rows[i].extend(row) for i in ridge]
                # Make Voronoi-to-Voronoi connections
                row.append(row[0])
                [am.rows[row[i]].append(row[i+1]) for i in range(len(row)-1)]
                # Ensure connections are made symmetrically
                [am.rows[row[i+1]].append(row[i]) for i in range(len(row)-1)]
        # Finalize adjacency matrix by assigning data values to each location
        am.data = am.rows  # Values don't matter, only shape, so use 'rows'
        # Retrieve upper triangle and convert to csr to remove duplicates
        am = sp.sparse.triu(A=am, k=1, format='csr')
        # Convert to COO format for OpenPNM compatibility
        am = am.tocoo()

        # Translate adjacency matrix and points to OpenPNM format
        coords = pts_all
        conns = sp.vstack((am.row, am.col)).T
        Np = sp.size(coords, axis=0)
        Nt = sp.size(conns, axis=0)
        self.update({'pore.all': sp.ones((Np, ), dtype=bool)})
        self.update({'throat.all': sp.ones((Nt, ), dtype=bool)})
        self['throat.conns'] = conns
        self['pore.coords'] = sp.around(coords, decimals=10)

        # Label all pores and throats by type
        self['pore.delaunay'] = False
        self['pore.delaunay'][0:Npts] = True
        self['pore.voronoi'] = False
        self['pore.voronoi'][Npts:] = True
        # Label throats between Delaunay pores
        self['throat.delaunay'] = False
        Ts = sp.all(self['throat.conns'] < Npts, axis=1)
        self['throat.delaunay'][Ts] = True
        # Label throats between Voronoi pores
        self['throat.voronoi'] = False
        Ts = sp.all(self['throat.conns'] >= Npts, axis=1)
        self['throat.voronoi'][Ts] = True
        # Label throats connecting a Delaunay and a Voronoi pore
        self['throat.interconnect'] = False
        Ts = self.throats(labels=['delaunay', 'voronoi'], mode='not')
        self['throat.interconnect'][Ts] = True

        # Trim all pores that lie outside of the specified domain
        if trim_domain:
            self._trim_domain(domain_size=domain_size)

    def _trim_domain(self, domain_size=None):
        r"""
        Trims pores that lie outside the specified domain.

        Parameters
        ----------
        domain_size : array_like
            The size and shape of the domain beyond which points should be
            trimmed. The argument is treated as follows:

            **sphere** : If a scalar or single element list is received, it's
            treated as the radius [r] of a sphere centered on [0, 0, 0].

            **cylinder** : If a two-element list is received it's treated as
            the radius and height of a cylinder [r, z] whose central axis
            starts at [0, 0, 0] and extends in the positive z-direction.

            **rectangle** : If a three element list is received, it's treated
            as the outer corner of rectangle [x, y, z] whose opposite corner
            lies at [0, 0, 0].

        Notes
        -----
        This function assumes that some Delaunay nodes exist outside the
        given ``domain_size``.  These points can either be the result of
        reflecting the base points or simply creating points beyond the
        domain.  Without these extra points the Voronoi network would contain
        points at inf.
        """
        # Label external pores for trimming below
        self['pore.external'] = False
        if len(domain_size) == 1:  # Spherical
            # Trim external Delaunay pores
            r = sp.sqrt(sp.sum(self['pore.coords']**2, axis=1))
            Ps = (r > domain_size)*self['pore.delaunay']
            self['pore.external'][Ps] = True
            # Trim external Voronoi pores
            Ps = ~self['pore.external']*self['pore.delaunay']
            Ps = self.find_neighbor_pores(pores=Ps)
            Ps = ~self.tomask(pores=Ps)*self['pore.voronoi']
            self['pore.external'][Ps] = True
        elif len(domain_size) == 2:  # Cylindrical
            # Trim external Delaunay pores outside radius
            r = sp.sqrt(sp.sum(self['pore.coords'][:, [0, 1]]**2, axis=1))
            Ps = (r > domain_size[0])*self['pore.delaunay']
            self['pore.external'][Ps] = True
            # Trim external Delaunay pores above and below cylinder
            Ps1 = self['pore.coords'][:, 2] > domain_size[1]
            Ps2 = self['pore.coords'][:, 2] < 0
            Ps = self['pore.delaunay']*(Ps1 + Ps2)
            self['pore.external'][Ps] = True
            # Trim external Voronoi pores
            Ps = ~self['pore.external']*self['pore.delaunay']
            Ps = self.find_neighbor_pores(pores=Ps)
            Ps = ~self.tomask(pores=Ps)*self['pore.voronoi']
            self['pore.external'][Ps] = True
        elif len(domain_size) == 3:  # Rectilinear
            # Trim external Delaunay pores
            Ps1 = sp.any(self['pore.coords'] > domain_size, axis=1)
            Ps2 = sp.any(self['pore.coords'] < [0, 0, 0], axis=1)
            Ps = self['pore.delaunay']*(Ps1 + Ps2)
            self['pore.external'][Ps] = True
            # Trim external Voronoi pores
            Ps = ~self['pore.external']*self['pore.delaunay']
            Ps = self.find_neighbor_pores(pores=Ps)
            Ps = ~self.tomask(pores=Ps)*self['pore.voronoi']
            self['pore.external'][Ps] = True

        # Begin process of removing, adjusting, and labeling pores
        self['pore.surface'] = False
        self['throat.surface'] = False

        # Label Delaunay pores on the surface
        Ps = self.pores('external', mode='not')
        Ps = self.find_neighbor_pores(pores=Ps)
        Ps = self.filter_by_label(pores=Ps, labels='delaunay')
        self['pore.surface'][Ps] = True
        self['pore.external'][Ps] = False  # So they aren't deleted below

        # Label Voronoi pores on surface
        Ps = self.pores('external')
        Ps = self.find_neighbor_pores(pores=Ps)
        Ps = self.filter_by_label(pores=Ps, labels='voronoi')
        self['pore.surface'][Ps] = True

        # Label Voronoi and interconnect throats on surface
        Ps = self.pores('surface')
        Ts = self.find_neighbor_throats(pores=Ps, mode='intersection')
        self['throat.surface'][Ts] = True

        # Trim external pores
        Ps = self.pores('external')
        self.trim(pores=Ps)

        # Trim throats between Delaunay surface pores
        Ps = self.pores(labels=['surface', 'delaunay'], mode='intersection')
        Ts = self.find_neighbor_throats(pores=Ps, mode='intersection')
        self.trim(throats=Ts)

        # Move Delaunay surface pores to centroid of Voronoi facet
        Ps = self.pores(labels=['surface', 'delaunay'], mode='intersection')
        for P in Ps:
            Ns = self.find_neighbor_pores(pores=P)
            Ns = self.filter_by_label(pores=Ns, labels='voronoi')
            coords = sp.mean(self['pore.coords'][Ns], axis=0)
            self['pore.coords'][P] = coords

        self['pore.internal'] = ~self['pore.surface']
        self['throat.internal'] = ~self['throat.surface']

        # Clean-up
        del self['pore.external']

    def find_throat_facets(self, throats=None):
        r"""
        Finds the coordinates of the Voronoi pores that define the facet or
        ridge between the pore-pairs associated with the given throat.

        Parameters
        ----------
        throats : array_like
            The throats whose facets are sought.  The given throats should be
            from the 'delaunay' network. If no throats are specified, all
            'delaunay' throats are assumed.

        Notes
        -----
        The method is not well optimized as it scans through each given throat
        inside a for-loop, so it could be slow for large networks.

        """
        if throats is None:
            throats = self.throats('delaunay')
        else:
            throats = self.filter_by_label(throats, labels='delaunay')
        if 'throat.facet_coords' not in self.keys():
            self['throat.facet_coords'] = sp.ndarray((self.Nt, ), dtype=object)
        tvals = self['throat.interconnect'].astype(int)
        am = self.create_adjacency_matrix(data=tvals, sprsfmt='lil')
        for t in throats:
            P12 = self['throat.conns'][t]
            Ps = list(set(am.rows[P12][0]).intersection(am.rows[P12][1]))
            if sp.size(Ps) > 0:
                self['throat.facet_coords'][t] = self['pore.coords'][Ps]

    def find_pore_hulls(self, pores=None):
        r"""
        Finds the coordinates of the Voronoi pores that define the convex hull
        around the given pores.

        Parameters
        ----------
        pores : array_like
            The pores whose convex hull are sought.  The given pores should be
            from the 'delaunay' network.  If no pores are given, then the hull
            is found for all 'delaunay' pores.

        Notes
        -----
        This metod is not fully optimized as it scans through each pore in a
        for-loop, so could be slow for large networks.
        """
        if pores is None:
            pores = self.pores('delaunay')
        else:
            pores = self.filter_by_label(pores, labels='delaunay')
        if 'pore.hull_coords' not in self.keys():
            self['pore.hull_coords'] = sp.ndarray((self.Np, ), dtype=object)
        tvals = self['throat.interconnect'].astype(int)
        am = self.create_adjacency_matrix(data=tvals, sprsfmt='lil')
        for p in pores:
            Ps = am.rows[p]
            if sp.size(Ps) > 0:
                self['pore.hull_coords'][p] = self['pore.coords'][Ps]
