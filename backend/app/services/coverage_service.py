"""Loaded crash-record coverage and analysis freshness checks."""

from dataclasses import dataclass
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session
from app.services.version_service import current_input_version


MISSING_DATA_MESSAGE = (
    "Crash records are not loaded for every selected year. "
    "Load the missing years before starting this analysis."
)
NO_DATA_MESSAGE = (
    "No crash records are loaded for the selected years. "
    "Load crash records and rerun this analysis."
)
STALE_DATA_MESSAGE = (
    "The source data or analysis methodology has changed, or this result predates version tracking. "
    "Rerun the analysis to refresh its results."
)


@dataclass(frozen=True)
class MunicipalityCoverage:
    """Positive loaded-record counts; this does not assert archive completeness."""

    muni_id: int
    crash_counts_by_year: Dict[int, int]

    @property
    def available_years(self) -> List[int]:
        return sorted(self.crash_counts_by_year)

    @property
    def total_crashes(self) -> int:
        return sum(self.crash_counts_by_year.values())

    def missing_years(self, start_year: int, end_year: int) -> List[int]:
        return [
            year
            for year in range(start_year, end_year + 1)
            if self.crash_counts_by_year.get(year, 0) <= 0
        ]

    def count_for_range(self, start_year: int, end_year: int) -> int:
        return sum(
            self.crash_counts_by_year.get(year, 0)
            for year in range(start_year, end_year + 1)
        )

    def as_response(self) -> Dict:
        return {
            "muni_id": self.muni_id,
            "available_years": self.available_years,
            "crash_counts_by_year": {
                str(year): self.crash_counts_by_year[year]
                for year in self.available_years
            },
            "total_crashes": self.total_crashes,
        }


@dataclass(frozen=True)
class AnalysisDataAssessment:
    data_status: str
    data_message: Optional[str]
    available_years: List[int]
    missing_years: List[int]
    current_total_crashes: int


class MissingCrashDataError(ValueError):
    """Raised when one or more requested years have no loaded crash records."""

    def __init__(self, missing_years: List[int], available_years: List[int]):
        self.detail = {
            "code": "missing_crash_data",
            "message": MISSING_DATA_MESSAGE,
            "missing_years": missing_years,
            "available_years": available_years,
        }
        super().__init__(MISSING_DATA_MESSAGE)


class AnalysisDataConflictError(ValueError):
    """Raised when completed results no longer match loaded crash records."""

    def __init__(self, assessment: AnalysisDataAssessment):
        self.detail = {
            "code": f"analysis_data_{assessment.data_status}",
            "message": assessment.data_message,
            "data_status": assessment.data_status,
            "missing_years": assessment.missing_years,
            "available_years": assessment.available_years,
        }
        super().__init__(assessment.data_message)


class CoverageService:
    """Calculate loaded-record availability from the crashes table."""

    def __init__(self, db: Session):
        self.db = db

    def get_municipality_coverage(self, muni_id: int) -> MunicipalityCoverage:
        rows = self.db.execute(
            text(
                """
                SELECT
                    EXTRACT(YEAR FROM crash_date)::integer AS crash_year,
                    COUNT(*)::integer AS crash_count
                FROM crashes
                WHERE muni_id = :muni_id
                GROUP BY EXTRACT(YEAR FROM crash_date)
                HAVING COUNT(*) > 0
                ORDER BY crash_year
                """
            ),
            {"muni_id": muni_id},
        ).all()
        return MunicipalityCoverage(
            muni_id=muni_id,
            crash_counts_by_year={row.crash_year: row.crash_count for row in rows},
        )

    def require_years_available(
        self,
        muni_id: int,
        start_year: int,
        end_year: int,
    ) -> MunicipalityCoverage:
        coverage = self.get_municipality_coverage(muni_id)
        missing_years = coverage.missing_years(start_year, end_year)
        if missing_years:
            raise MissingCrashDataError(missing_years, coverage.available_years)
        return coverage

    def assess_analysis(self, analysis) -> AnalysisDataAssessment:
        coverage = self.get_municipality_coverage(analysis.muni_id)
        missing_years = coverage.missing_years(
            analysis.start_year,
            analysis.end_year,
        )
        current_total = coverage.count_for_range(
            analysis.start_year,
            analysis.end_year,
        )

        if analysis.status != "completed":
            data_status = "ready"
            data_message = None
        elif current_total == 0:
            data_status = "no_data"
            data_message = NO_DATA_MESSAGE
        elif missing_years:
            data_status = "missing_years"
            data_message = MISSING_DATA_MESSAGE
        elif analysis.total_crashes != current_total:
            data_status = "stale"
            data_message = STALE_DATA_MESSAGE
        elif not getattr(analysis, 'input_version', None) or analysis.input_version != current_input_version(self.db):
            data_status = "stale"
            data_message = STALE_DATA_MESSAGE
        else:
            data_status = "ready"
            data_message = None

        return AnalysisDataAssessment(
            data_status=data_status,
            data_message=data_message,
            available_years=coverage.available_years,
            missing_years=missing_years,
            current_total_crashes=current_total,
        )

    def require_current_results(self, analysis) -> AnalysisDataAssessment:
        assessment = self.assess_analysis(analysis)
        if analysis.status == "completed" and assessment.data_status != "ready":
            raise AnalysisDataConflictError(assessment)
        return assessment
