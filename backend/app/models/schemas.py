from pydantic import BaseModel, Field, ConfigDict, model_validator
from datetime import date, datetime
from typing import Optional, List, Dict, Any
from enum import Enum


class SeverityLevel(str, Enum):
    """Crash severity levels."""
    FATAL = "fatal"
    SERIOUS_INJURY = "serious_injury"
    MINOR_INJURY = "minor_injury"
    PROPERTY_DAMAGE = "property_damage"


class AnalysisStatus(str, Enum):
    """Analysis status values."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class GeoJSONGeometry(BaseModel):
    """GeoJSON geometry object."""
    type: str
    coordinates: List[Any]


class GeoJSONFeature(BaseModel):
    """GeoJSON feature object."""
    type: str = "Feature"
    geometry: GeoJSONGeometry
    properties: Dict[str, Any]


class GeoJSONFeatureCollection(BaseModel):
    """GeoJSON feature collection."""
    type: str = "FeatureCollection"
    features: List[GeoJSONFeature]


# Municipality Schemas

class MunicipalityBase(BaseModel):
    """Base municipality schema."""
    name: str
    county: str
    muni_code: Optional[str] = None


class MunicipalityCreate(MunicipalityBase):
    """Schema for creating a municipality."""
    pass


class MunicipalityResponse(MunicipalityBase):
    """Schema for municipality response."""
    muni_id: int

    model_config = ConfigDict(from_attributes=True)


class MunicipalityDetail(MunicipalityResponse):
    """Detailed municipality information with statistics."""
    total_crashes: Optional[int] = 0
    total_fatalities: Optional[int] = 0
    total_injuries: Optional[int] = 0
    road_miles: Optional[float] = 0
    latest_analysis: Optional[datetime] = None


# Crash Schemas

class CrashBase(BaseModel):
    """Base crash schema."""
    crash_date: date
    severity: SeverityLevel
    ped_involved: bool = False
    bike_involved: bool = False
    road_name: Optional[str] = None


class CrashCreate(CrashBase):
    """Schema for creating a crash record."""
    external_id: Optional[str] = None
    crash_time: Optional[str] = None
    route_number: Optional[str] = None
    contributing_factors: Optional[str] = None
    weather_condition: Optional[str] = None
    light_condition: Optional[str] = None
    latitude: float
    longitude: float
    geocode_quality: Optional[str] = "medium"


class CrashResponse(CrashBase):
    """Schema for crash response."""
    crash_id: int
    external_id: Optional[str] = None
    muni_id: int
    segment_id: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


# Analysis Schemas

class AnalysisConfig(BaseModel):
    """Configuration for running an analysis."""
    start_year: int = Field(..., ge=2000, le=2030)
    end_year: int = Field(..., ge=2000, le=2030)
    snap_distance_meters: float = Field(default=50.0, ge=10, le=200)
    segment_length_miles: float = Field(default=0.1, ge=0.05, le=1.0)
    significance_threshold: float = Field(default=0.05, ge=0.01, le=0.2)

    @model_validator(mode='after')
    def validate_years(self):
        """Ensure start_year <= end_year."""
        if self.start_year > self.end_year:
            raise ValueError("start_year must be less than or equal to end_year")
        return self


class AnalysisCreate(BaseModel):
    """Schema for creating an analysis."""
    muni_id: int
    config: AnalysisConfig


class AnalysisResponse(BaseModel):
    """Schema for analysis response."""
    analysis_id: int
    muni_id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    start_year: int
    end_year: int
    status: AnalysisStatus
    error_message: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AnalysisDetail(AnalysisResponse):
    """Detailed analysis results."""
    total_crashes: Optional[int] = 0
    total_fatalities: Optional[int] = 0
    total_injuries: Optional[int] = 0
    hin_miles: Optional[float] = 0
    hin_segment_count: Optional[int] = 0
    municipality_name: Optional[str] = None


class AnalysisSummary(BaseModel):
    """Summary statistics for an analysis."""
    total_crashes: int
    fatal_crashes: int
    serious_injury_crashes: int
    minor_injury_crashes: int
    property_damage_crashes: int
    ped_crashes: int
    bike_crashes: int
    hin_miles: float
    hin_corridors: int
    vulnerable_tract_percentage: float


# HIN Segment Schemas

class HINSegmentBase(BaseModel):
    """Base HIN segment schema."""
    crash_count_total: int
    severity_score: float
    crash_rate: float
    p_value: Optional[float] = None
    is_significant: bool


class HINSegmentResponse(HINSegmentBase):
    """Schema for HIN segment response."""
    hin_id: int
    segment_id: int
    analysis_id: int
    crash_count_fatal: int
    crash_count_serious_injury: int
    crash_count_minor_injury: int
    crash_count_ped: int
    crash_count_bike: int
    corridor_id: Optional[int] = None
    corridor_name: Optional[str] = None
    in_vulnerable_tract: bool
    is_ped_hin: bool
    is_bike_hin: bool
    is_general_hin: bool

    model_config = ConfigDict(from_attributes=True)


class HINCorridorSummary(BaseModel):
    """Summary of a HIN corridor."""
    corridor_id: int
    corridor_name: str
    segment_count: int
    total_length_miles: float
    total_crashes: int
    total_fatalities: int
    avg_crash_rate: float
    road_names: List[str]
    in_vulnerable_area: bool


# Report Schemas

class ReportConfig(BaseModel):
    """Configuration for PDF report generation."""
    include_maps: bool = True
    include_data_tables: bool = True
    include_equity_analysis: bool = True
    include_ped_bike_analysis: bool = True
    map_style: str = "street"  # street, satellite, hybrid


class ReportRequest(BaseModel):
    """Request for generating a report."""
    analysis_id: int
    config: ReportConfig = ReportConfig()


# Error Schemas

class ErrorResponse(BaseModel):
    """Standard error response."""
    error: str
    detail: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# Health Check

class HealthCheck(BaseModel):
    """Health check response."""
    status: str
    version: str
    database: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
