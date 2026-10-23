import { useCallback, useState } from 'react'
import { api } from '../api/client'
import { Job } from '../api/schemas'
import { usePoll } from './usePoll'

/**
 * Follow a background job (/api/job/{id}) while it runs, at the original page's 0.7 s cadence.
 * Polling stops once the job is done, failed, cancelled or paused; its last state stays available.
 */
export function useJob(id: string | undefined) {
  const [finished, setFinished] = useState<Job>()
  const load = useCallback(async () => {
    const j = await api(Job, `/api/job/${id ?? ''}`)
    if (j.status !== 'running') setFinished(j)
    return j
  }, [id])
  const over = !!id && finished?.id === id
  const poll = usePoll(load, 700, !!id && !over)
  const job = over ? finished : poll.data?.id === id ? poll.data : undefined
  return { job, running: !!id && (!job || job.status === 'running'), error: id && !over ? poll.error : '' }
}
