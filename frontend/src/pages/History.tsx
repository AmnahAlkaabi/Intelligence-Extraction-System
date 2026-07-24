import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listJobs } from "../api/client";
import type { Job } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";

export default function HistoryPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listJobs().then(setJobs).finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="page-narrow">Loading…</div>;

  return (
    <div className="page-narrow">
      <h1 className="page-title">Job History</h1>
      {jobs.length === 0 && <p className="page-sub">No analyses run yet.</p>}
      <div className="job-list">
        {jobs.map((job) => (
          <Link key={job.job_id} to={`/jobs/${job.job_id}`} className="job-row">
            <span className="job-id">{job.name || job.job_id}</span>
            <span className="job-files">{job.files.length} file(s)</span>
            <StatusBadge status={job.status} />
            <span className="job-date">{new Date(job.created_at).toLocaleString()}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
