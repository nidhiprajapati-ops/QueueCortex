import { Clock, Search } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useTagMappings } from '@/hooks/useTags'
import { useRosterOverdueTickets } from '@/hooks/useRoster'

interface Props {
  search: string
  onSearchChange: (v: string) => void
  derivedType: string
  onDerivedTypeChange: (v: string) => void
  sort: string
  onSortChange: (v: string) => void
  searchInputRef?: React.RefObject<HTMLInputElement | null>
}

export function FilterBar({ search, onSearchChange, derivedType, onDerivedTypeChange, sort, onSortChange, searchInputRef }: Props) {
  const { data: mappings } = useTagMappings()
  const types = Array.from(new Set((mappings ?? []).map((m) => m.type_label))).sort()
  const { data: overdue } = useRosterOverdueTickets()
  const overdueCount = overdue?.length ?? 0

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative w-full max-w-xs">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          ref={searchInputRef}
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="Search customer or ticket # (/)"
          className="rounded-full pl-9"
        />
      </div>
      <Select value={derivedType || 'all'} onValueChange={(v) => onDerivedTypeChange(v === 'all' ? '' : v)}>
        <SelectTrigger className="w-40">
          <SelectValue placeholder="Type" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All types</SelectItem>
          {types.map((t) => (
            <SelectItem key={t} value={t}>
              {t}
            </SelectItem>
          ))}
          <SelectItem value="Uncategorized">Uncategorized</SelectItem>
        </SelectContent>
      </Select>
      <Select value={sort} onValueChange={onSortChange}>
        <SelectTrigger className="w-52">
          <SelectValue placeholder="Sort" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="last_event_at:desc">Most recent activity</SelectItem>
          <SelectItem value="num:desc">Ticket number (newest)</SelectItem>
          <SelectItem value="created_at:asc">Oldest open first</SelectItem>
        </SelectContent>
      </Select>
      <Link
        to="/shift-watch"
        className="inline-flex h-9 items-center gap-1.5 rounded-full border border-border bg-secondary/60 px-3.5 text-sm font-medium text-foreground transition-colors hover:bg-secondary"
      >
        <Clock className="size-4" />
        Shift Watch
        {overdueCount > 0 && (
          <span className="font-tabular inline-flex h-4.5 min-w-4.5 items-center justify-center rounded-full bg-red px-1 text-[11px] font-semibold text-destructive">
            {overdueCount}
          </span>
        )}
      </Link>
    </div>
  )
}
