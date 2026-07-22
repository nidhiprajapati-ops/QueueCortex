import { useMemo, useRef, useState } from 'react'
import { AlertTriangle, ChevronDown, ExternalLink, Loader2, Search, Upload } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useToast } from '@/components/ui/toast'
import { TicketHistoryPanel } from '@/components/tickets/TicketHistoryPanel'
import { useRosterAgents, useRosterOverdueTickets, useRosterTicketDetail, useUpdateRosterShift, useUploadRoster } from '@/hooks/useRoster'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { absoluteTime, relativeTime } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { RosterOverdueTicket, ShiftReason } from '@/types'

const COLUMNS = ['#', 'Holding', 'Type', 'Shift', 'Held', 'Last activity', '']
const ROSTER_SHIFT_CODES = ['6A-3P', '11A-8P', '9P-6A', 'Off']

function formatShiftLabel(shiftCode: string | null, reason: ShiftReason): string {
  if (!shiftCode) return '—'
  if (reason === 'before_shift_start') return `${shiftCode} (not started)`
  if (reason === 'shift_ended') return `${shiftCode} (ended)`
  return shiftCode
}

function ShiftWatchRow({ ticket, expanded, onToggle }: { ticket: RosterOverdueTicket; expanded: boolean; onToggle: () => void }) {
  const { data: detail, isLoading } = useRosterTicketDetail(expanded ? ticket.id : null)

  return (
    <>
      <TableRow className={cn(ticket.is_associate_or_trainer && 'bg-red/50 hover:bg-red/70')}>
        <TableCell className="font-mono text-xs">
          {ticket.trinity_url ? (
            <a href={ticket.trinity_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:underline">
              #{ticket.num} <ExternalLink className="size-3 opacity-60" />
            </a>
          ) : (
            `#${ticket.num}`
          )}
        </TableCell>
        <TableCell className="max-w-45 truncate text-sm">
          <div className="flex flex-col">
            <span>{ticket.agent_name}</span>
            <span className="text-xs text-muted-foreground">{ticket.agent_role}</span>
          </div>
        </TableCell>
        <TableCell>
          <div className="flex items-center gap-1.5">
            {ticket.derived_type ? <Badge variant="secondary">{ticket.derived_type}</Badge> : <span className="text-xs text-muted-foreground">—</span>}
            {ticket.alert_tags.length > 0 && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-flex shrink-0 items-center text-destructive">
                    <AlertTriangle className="size-4" />
                  </span>
                </TooltipTrigger>
                <TooltipContent>Flagged tag{ticket.alert_tags.length > 1 ? 's' : ''}: {ticket.alert_tags.join(', ')}</TooltipContent>
              </Tooltip>
            )}
          </div>
        </TableCell>
        <TableCell className="text-xs whitespace-nowrap text-muted-foreground">{formatShiftLabel(ticket.shift_code, ticket.reason)}</TableCell>
        <TableCell className="font-tabular text-xs whitespace-nowrap text-muted-foreground">
          {ticket.held_since ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <span>{relativeTime(ticket.held_since)}</span>
              </TooltipTrigger>
              <TooltipContent>Since {absoluteTime(ticket.held_since)}</TooltipContent>
            </Tooltip>
          ) : (
            '—'
          )}
        </TableCell>
        <TableCell className="font-tabular text-xs whitespace-nowrap text-muted-foreground">{relativeTime(ticket.last_event_at)}</TableCell>
        <TableCell>
          <button onClick={onToggle} className="rounded-full p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground" aria-label="Toggle history">
            <ChevronDown className={cn('size-4 transition-transform duration-200', expanded && 'rotate-180')} />
          </button>
        </TableCell>
      </TableRow>
      {expanded && (
        <TableRow className="hover:bg-transparent">
          <TableCell colSpan={COLUMNS.length} className="p-0">
            <div className="mx-2 my-2 rounded-xl bg-muted/50 dark:bg-muted/30">
              <TicketHistoryPanel ticketId={ticket.id} detail={detail} isLoading={isLoading} noteLabel="Last internal note (Trinity)" />
            </div>
          </TableCell>
        </TableRow>
      )}
    </>
  )
}

function EditableShiftCell({ email, which, value }: { email: string; which: 'today' | 'tomorrow'; value: string | null }) {
  const updateShift = useUpdateRosterShift()
  const toast = useToast()
  // The uploaded sheet can carry leave codes we don't hardcode (e.g. "EL") -
  // keep whatever's already set as a selectable option so editing the other
  // column never silently drops it.
  const options = value && !ROSTER_SHIFT_CODES.includes(value) ? [value, ...ROSTER_SHIFT_CODES] : ROSTER_SHIFT_CODES

  return (
    <Select
      value={value ?? undefined}
      onValueChange={(v) => {
        updateShift.mutate(
          { email, which, shiftCode: v },
          {
            onError: (err) => {
              toast({ title: 'Could not update shift', description: err instanceof Error ? err.message : undefined, variant: 'error' })
            },
          },
        )
      }}
    >
      <SelectTrigger className="h-7 w-27 gap-1 border-none bg-transparent px-1.5 font-tabular text-xs hover:bg-accent focus-visible:ring-1">
        <SelectValue placeholder="—" />
      </SelectTrigger>
      <SelectContent>
        {options.map((c) => (
          <SelectItem key={c} value={c}>
            {c}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 4 }).map((_, i) => (
        <TableRow key={i}>
          <TableCell colSpan={COLUMNS.length}>
            <Skeleton className="h-5 w-full" />
          </TableCell>
        </TableRow>
      ))}
    </>
  )
}

function RosterUpload({ compact }: { compact?: boolean }) {
  const uploadRoster = useUploadRoster()
  const toast = useToast()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    uploadRoster.mutate(file, {
      onSuccess: (result) => {
        toast({
          title: 'Roster uploaded',
          description: `${result.agents} agents, ${result.shift_rows} shift rows (${result.date_range[0] ?? '?'} – ${result.date_range[1] ?? '?'})`,
          variant: 'success',
        })
      },
      onError: (err) => {
        toast({ title: 'Upload failed', description: err instanceof Error ? err.message : undefined, variant: 'error' })
      },
    })
  }

  return (
    <div className="flex items-center gap-3">
      <input ref={fileInputRef} type="file" accept=".csv,.xlsx,.xlsm" className="hidden" onChange={handleFileChange} />
      <Button onClick={() => fileInputRef.current?.click()} disabled={uploadRoster.isPending} size={compact ? 'sm' : 'default'}>
        {uploadRoster.isPending ? <Loader2 className="size-4 animate-spin" /> : <Upload className="size-4" />}
        {compact ? 'Update roster' : 'Upload roster (CSV or Excel)'}
      </Button>
    </div>
  )
}

export function ShiftWatch() {
  const { data: tickets, isLoading } = useRosterOverdueTickets()
  const { data: agents } = useRosterAgents()
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const debouncedSearch = useDebouncedValue(search, 200)

  const filteredTickets = useMemo(() => {
    if (!tickets) return []
    const q = debouncedSearch.trim().toLowerCase()
    if (!q) return tickets
    return tickets.filter((t) => t.agent_name.toLowerCase().includes(q) || String(t.num).includes(q))
  }, [tickets, debouncedSearch])

  const hasRoster = agents && agents.length > 0

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-[28px] font-bold tracking-[-0.015em]">Shift Watch</h1>
          <p className="text-sm text-muted-foreground">
            Tickets held by an L2 agent who's currently off-shift — ended, not started yet today, or off/on leave.
          </p>
        </div>
        {hasRoster && <RosterUpload compact />}
      </div>

      {!hasRoster ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-4 py-14 text-center">
            <div>
              <p className="text-base font-semibold">Add your team's roster to get started</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Upload the shift-roster file (CSV or Excel — agent, role, and per-day shift codes) to see who's holding tickets while off-shift.
              </p>
            </div>
            <RosterUpload />
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="relative w-full max-w-xs">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search agent or ticket #"
              className="rounded-full pl-9"
            />
          </div>

          <Card className="overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  {COLUMNS.map((c) => (
                    <TableHead key={c}>{c}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  <SkeletonRows />
                ) : filteredTickets.length > 0 ? (
                  filteredTickets.map((t) => (
                    <ShiftWatchRow key={t.id} ticket={t} expanded={expandedId === t.id} onToggle={() => setExpandedId(expandedId === t.id ? null : t.id)} />
                  ))
                ) : (
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={COLUMNS.length} className="py-8 text-center text-sm text-muted-foreground">
                      {debouncedSearch ? 'No matches for that search.' : 'Nothing off-shift right now.'}
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Roster</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-hidden rounded-lg border border-border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Agent</TableHead>
                      <TableHead>Role</TableHead>
                      <TableHead>Today</TableHead>
                      <TableHead>Tomorrow</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {agents.map((a) => (
                      <TableRow key={a.email}>
                        <TableCell>{a.name}</TableCell>
                        <TableCell className="text-muted-foreground">{a.role}</TableCell>
                        <TableCell className="p-1">
                          <EditableShiftCell email={a.email} which="today" value={a.today_shift_code} />
                        </TableCell>
                        <TableCell className="p-1">
                          <EditableShiftCell email={a.email} which="tomorrow" value={a.tomorrow_shift_code} />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
