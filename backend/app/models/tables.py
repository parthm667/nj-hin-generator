from sqlalchemy import (
    ARRAY,
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB
from geoalchemy2 import Geometry
from datetime import datetime
from app.models.database import Base


class Municipality(Base):
    """Municipal boundary and information."""

    __tablename__ = "municipalities"

    muni_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, index=True)
    county = Column(String(50), nullable=False)
    muni_code = Column(String(20), unique=True)  # Official NJ municipality code
    geom = Column(Geometry(geometry_type='MULTIPOLYGON', srid=4326, spatial_index=False), nullable=False)

    # Relationships
    road_segments = relationship("RoadSegment", back_populates="municipality")
    crashes = relationship("Crash", back_populates="municipality")
    analyses = relationship("Analysis", back_populates="municipality")
    census_tracts = relationship("CensusTract", back_populates="municipality")

    # Spatial index
    __table_args__ = (
        Index('idx_municipalities_geom', 'geom', postgresql_using='gist'),
    )

    def __repr__(self):
        return f"<Municipality(name='{self.name}', county='{self.county}')>"


class RoadSegment(Base):
    """Road network segments for analysis."""

    __tablename__ = "road_segments"

    segment_id = Column(Integer, primary_key=True, autoincrement=True)
    osm_id = Column(Integer, index=True)
    source_id = Column(String(200), nullable=True)
    sri = Column(String(20), nullable=True)
    road_name = Column(String(200))
    road_type = Column(String(50))  # highway type from OSM (primary, secondary, residential, etc.)
    road_class = Column(String(20))  # Simplified: arterial, collector, local
    length_miles = Column(Float, nullable=False)
    muni_id = Column(Integer, ForeignKey('municipalities.muni_id'), nullable=False)
    geom = Column(Geometry(geometry_type='LINESTRING', srid=4326, spatial_index=False), nullable=False)

    # Relationships
    municipality = relationship("Municipality", back_populates="road_segments")
    crashes = relationship("Crash", back_populates="segment")
    hin_segments = relationship("HINSegment", back_populates="segment")

    # Indexes
    __table_args__ = (
        Index('idx_segments_geom', 'geom', postgresql_using='gist'),
        Index('idx_segments_muni', 'muni_id'),
        Index('idx_segments_road_class', 'road_class'),
        Index('idx_segments_source_id', 'source_id', unique=True),
        Index('idx_segments_sri', 'sri'),
    )

    def __repr__(self):
        return f"<RoadSegment(id={self.segment_id}, name='{self.road_name}', type='{self.road_type}')>"


class RoadRoute(Base):
    """Unsplit NJDOT path retaining calibrated milepost measures."""

    __tablename__ = "road_routes"

    source_id = Column(String(200), primary_key=True)
    sri = Column(String(20), nullable=False)
    mp_start = Column(Float, nullable=False)
    mp_end = Column(Float, nullable=False)
    geom = Column(
        Geometry(
            geometry_type='LINESTRINGM',
            srid=4326,
            dimension=3,
            spatial_index=False,
        ),
        nullable=False,
    )

    __table_args__ = (
        Index('idx_road_routes_sri', 'sri'),
        Index('idx_road_routes_geom', 'geom', postgresql_using='gist'),
    )

    def __repr__(self):
        return (
            f"<RoadRoute(source_id='{self.source_id}', sri='{self.sri}', "
            f"mileposts={self.mp_start}-{self.mp_end})>"
        )


class Crash(Base):
    """Crash incident records."""

    __tablename__ = "crashes"

    crash_id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(String(50), unique=True)  # ID from source data
    crash_date = Column(Date, nullable=False, index=True)
    crash_time = Column(String(10))
    # fatal, serious_injury, minor_injury, injury_unknown, property_damage
    severity = Column(String(20), nullable=False, index=True)
    ped_involved = Column(Boolean, default=False, index=True)
    bike_involved = Column(Boolean, nullable=True, index=True)
    total_killed = Column(Integer, nullable=True)
    total_injured = Column(Integer, nullable=True)
    pedestrians_killed = Column(Integer, nullable=True)
    pedestrians_injured = Column(Integer, nullable=True)

    # Location info
    muni_id = Column(Integer, ForeignKey('municipalities.muni_id'), nullable=False)
    segment_id = Column(Integer, ForeignKey('road_segments.segment_id'))
    snap_distance = Column(Float)  # Distance in meters from crash point to assigned segment
    road_name = Column(String(200))
    route_number = Column(String(20))

    # Additional crash details
    contributing_factors = Column(String(500))
    weather_condition = Column(String(50))
    light_condition = Column(String(50))

    # Geometry
    geom = Column(Geometry(geometry_type='POINT', srid=4326, spatial_index=False), nullable=False)

    # Data quality flags
    geocode_quality = Column(String(20))  # high, medium, low

    # Relationships
    municipality = relationship("Municipality", back_populates="crashes")
    segment = relationship("RoadSegment", back_populates="crashes")

    # Indexes
    __table_args__ = (
        Index('idx_crashes_geom', 'geom', postgresql_using='gist'),
        Index('idx_crashes_date', 'crash_date'),
        Index('idx_crashes_muni', 'muni_id'),
        Index('idx_crashes_segment', 'segment_id'),
        Index('idx_crashes_severity', 'severity'),
        CheckConstraint(
            'total_killed IS NULL OR total_killed >= 0',
            name='ck_crashes_total_killed_nonnegative',
        ),
        CheckConstraint(
            'total_injured IS NULL OR total_injured >= 0',
            name='ck_crashes_total_injured_nonnegative',
        ),
        CheckConstraint(
            'pedestrians_killed IS NULL OR pedestrians_killed >= 0',
            name='ck_crashes_pedestrians_killed_nonnegative',
        ),
        CheckConstraint(
            'pedestrians_injured IS NULL OR pedestrians_injured >= 0',
            name='ck_crashes_pedestrians_injured_nonnegative',
        ),
    )

    def __repr__(self):
        return f"<Crash(id={self.crash_id}, date={self.crash_date}, severity='{self.severity}')>"


class CensusTract(Base):
    """Census tract boundaries and demographic data."""

    __tablename__ = "census_tracts"

    tract_id = Column(String(20), primary_key=True)
    muni_id = Column(Integer, ForeignKey('municipalities.muni_id'))
    county_fips = Column(String(5))

    # Demographic data
    total_population = Column(Integer)
    median_income = Column(Integer)
    pct_below_poverty = Column(Float)
    pct_no_vehicle = Column(Float)
    pct_minority = Column(Float)

    # Vulnerability indices
    svi_score = Column(Float)  # CDC Social Vulnerability Index
    svi_percentile = Column(Float)
    ejscreen_score = Column(Float)  # EPA Environmental Justice Screen
    source_year = Column(Integer)
    source_name = Column(String(200))

    # Geometry
    geom = Column(Geometry(geometry_type='MULTIPOLYGON', srid=4326, spatial_index=False), nullable=False)

    # Relationships
    municipality = relationship("Municipality", back_populates="census_tracts")

    # Indexes
    __table_args__ = (
        Index('idx_census_geom', 'geom', postgresql_using='gist'),
        Index('idx_census_muni', 'muni_id'),
    )

    def __repr__(self):
        return f"<CensusTract(id='{self.tract_id}', svi={self.svi_score})>"


class Analysis(Base):
    """Analysis run metadata and configuration."""

    __tablename__ = "analyses"

    analysis_id = Column(Integer, primary_key=True, autoincrement=True)
    muni_id = Column(Integer, ForeignKey('municipalities.muni_id'), nullable=False)
    created_at = Column(TIMESTAMP, default=datetime.utcnow, nullable=False)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Analysis parameters
    start_year = Column(Integer, nullable=False)
    end_year = Column(Integer, nullable=False)
    years_included = Column(ARRAY(Integer))

    # Configuration
    snap_distance_meters = Column(Float)
    segment_length_miles = Column(Float)
    significance_threshold = Column(Float)

    # Status
    status = Column(String(20), default='pending')  # pending, running, completed, failed
    error_message = Column(String(500))
    input_version = Column(JSONB)

    # Results summary
    total_crashes = Column(Integer)
    total_fatalities = Column(Integer)
    total_injuries = Column(Integer)
    hin_miles = Column(Float)
    hin_segment_count = Column(Integer)

    # Relationships
    municipality = relationship("Municipality", back_populates="analyses")
    hin_segments = relationship("HINSegment", back_populates="analysis")

    # Indexes
    __table_args__ = (
        Index('idx_analyses_muni', 'muni_id'),
        Index('idx_analyses_status', 'status'),
        Index('idx_analyses_created', 'created_at'),
    )

    def __repr__(self):
        return f"<Analysis(id={self.analysis_id}, muni_id={self.muni_id}, status='{self.status}')>"


class HINSegment(Base):
    """High Injury Network segment results."""

    __tablename__ = "hin_segments"

    hin_id = Column(Integer, primary_key=True, autoincrement=True)
    segment_id = Column(Integer, ForeignKey('road_segments.segment_id'), nullable=False)
    analysis_id = Column(Integer, ForeignKey('analyses.analysis_id'), nullable=False)

    # Crash statistics
    crash_count_total = Column(Integer, default=0)
    crash_count_fatal = Column(Integer, default=0)
    crash_count_serious_injury = Column(Integer, default=0)
    crash_count_minor_injury = Column(Integer, default=0)
    crash_count_ped = Column(Integer, default=0)
    crash_count_bike = Column(Integer, default=0)

    # Analysis results
    severity_score = Column(Float, nullable=False)
    crash_rate = Column(Float, nullable=False)  # crashes per mile per year
    expected_crashes = Column(Float)
    p_value = Column(Float)
    is_significant = Column(Boolean, default=False)

    # Network grouping
    corridor_id = Column(Integer)  # Groups connected segments into corridors
    corridor_name = Column(String(200))

    # Equity overlay
    in_vulnerable_tract = Column(Boolean, default=False)
    avg_svi_score = Column(Float)

    # Crash type flags
    is_ped_hin = Column(Boolean, default=False)
    is_bike_hin = Column(Boolean, default=False)
    is_general_hin = Column(Boolean, default=False)

    # Relationships
    segment = relationship("RoadSegment", back_populates="hin_segments")
    analysis = relationship("Analysis", back_populates="hin_segments")

    # Indexes
    __table_args__ = (
        Index('idx_hin_analysis', 'analysis_id'),
        Index('idx_hin_segment', 'segment_id'),
        Index('idx_hin_corridor', 'corridor_id'),
        Index('idx_hin_significant', 'is_significant'),
    )

    def __repr__(self):
        return f"<HINSegment(id={self.hin_id}, segment_id={self.segment_id}, rate={self.crash_rate:.2f})>"
