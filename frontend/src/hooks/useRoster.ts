import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { request } from '@/lib/api'
import type { RosterAgentOut, RosterOverdueTicket, RosterUploadResult, TicketDetail } from '@/types'

export function useRosterOverdueTickets() {
  return useQuery({
    queryKey: ['roster-overdue-tickets'],
    queryFn: () => request<RosterOverdueTicket[]>('/roster/overdue-tickets'),
    refetchInterval: 60_000,
  })
}

export function useRosterAgents() {
  return useQuery({
    queryKey: ['roster-agents'],
    queryFn: () => request<RosterAgentOut[]>('/roster/agents'),
  })
}

export function useRosterTicketDetail(ticketId: string | null) {
  return useQuery({
    queryKey: ['roster-ticket', ticketId],
    queryFn: () => request<TicketDetail>(`/roster/tickets/${ticketId}`),
    enabled: !!ticketId,
  })
}

async function uploadRosterCsv(file: File): Promise<RosterUploadResult> {
  const formData = new FormData()
  formData.append('file', file)
  // Deliberately not using lib/api.ts's request() here - it hardcodes
  // Content-Type: application/json, which breaks multipart uploads (the
  // browser needs to set its own boundary-bearing Content-Type).
  const res = await fetch('/api/v1/roster/upload', {
    method: 'POST',
    credentials: 'include',
    body: formData,
  })
  if (!res.ok) {
    const body = await res.text()
    let message = body
    try {
      message = JSON.parse(body).detail ?? body
    } catch {
      /* not json */
    }
    throw new Error(message || res.statusText)
  }
  return res.json()
}

export function useUploadRoster() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: uploadRosterCsv,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['roster-agents'] })
      qc.invalidateQueries({ queryKey: ['roster-overdue-tickets'] })
    },
  })
}

export function useUpdateRosterShift() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ email, which, shiftCode }: { email: string; which: 'today' | 'tomorrow'; shiftCode: string }) =>
      request<RosterAgentOut>(`/roster/agents/${encodeURIComponent(email)}/shift`, {
        method: 'PUT',
        body: JSON.stringify({ which, shift_code: shiftCode }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['roster-agents'] })
      qc.invalidateQueries({ queryKey: ['roster-overdue-tickets'] })
    },
  })
}
