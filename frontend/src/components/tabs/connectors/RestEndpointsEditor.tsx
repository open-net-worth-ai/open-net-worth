import { useState } from 'react'
import { Button, Checkbox, Cluster, FormField, Select, Stack, Text, TextField } from '../../ui'
import { REST_METHODS } from './restApi'
import type { RestEndpoint, RestMethod, RestParam, RestParamPlace } from './restApi'

// The endpoint list of one REST API. Each endpoint becomes a tool of its own for Lucius, with
// its parameters as the tool's arguments and its own permission level. Filled by reading an
// OpenAPI document, typed by hand, or both — every row is editable either way. A saved
// endpoint carries its `tool` id through an edit untouched, so the tool keeps its name.

const METHOD_OPTIONS = REST_METHODS.map((m) => ({ value: m, label: m }))
const PLACE_OPTIONS: { value: RestParamPlace; label: string }[] = [
  { value: 'query', label: 'Query' },
  { value: 'path', label: 'Path' },
  { value: 'body', label: 'Body' },
]

function newEndpoint(): RestEndpoint {
  return { method: 'GET', path: '', description: '', params: [] }
}

function newParam(): RestParam {
  return { name: '', in: 'query', required: false, description: '' }
}

interface ParamRowProps {
  id: string
  param: RestParam
  onChange: (next: RestParam) => void
  onRemove: () => void
}

function ParamRow({ id, param, onChange, onRemove }: ParamRowProps) {
  return (
    <div className="rest-param-row">
      <TextField
        aria-label="Parameter name"
        placeholder="name"
        value={param.name}
        onChange={(e) => onChange({ ...param, name: e.target.value })}
      />
      <Select
        aria-label="Where the parameter goes"
        dense
        options={PLACE_OPTIONS}
        value={param.in}
        onChange={(e) => onChange({ ...param, in: e.target.value as RestParamPlace })}
      />
      <TextField
        aria-label="What the parameter means"
        placeholder="What it means, e.g. start date, YYYY-MM-DD"
        value={param.description}
        onChange={(e) => onChange({ ...param, description: e.target.value })}
      />
      <Checkbox
        id={`${id}-req`}
        label="Required"
        checked={param.required}
        onChange={(e) => onChange({ ...param, required: e.target.checked })}
      />
      <Button size="sm" variant="ghost" aria-label="Remove parameter" onClick={onRemove}>
        Remove
      </Button>
    </div>
  )
}

interface EndpointFormProps {
  id: string
  endpoint: RestEndpoint
  onChange: (next: RestEndpoint) => void
  onDone: () => void
}

function EndpointForm({ id, endpoint, onChange, onDone }: EndpointFormProps) {
  const setParam = (i: number, next: RestParam) =>
    onChange({ ...endpoint, params: endpoint.params.map((p, j) => (j === i ? next : p)) })
  return (
    <div className="rest-ep-form">
      <Stack gap="--space-3">
        <div className="rest-ep-line">
          <Select
            aria-label="Method"
            dense
            options={METHOD_OPTIONS}
            value={endpoint.method}
            onChange={(e) => onChange({ ...endpoint, method: e.target.value as RestMethod })}
          />
          <TextField
            aria-label="Path"
            placeholder="/accounts/{account_id}/transactions"
            value={endpoint.path}
            autoFocus={!endpoint.path}
            onChange={(e) => onChange({ ...endpoint, path: e.target.value })}
          />
        </div>
        <FormField label="What it returns or does" htmlFor={`${id}-desc`}>
          <TextField
            id={`${id}-desc`}
            placeholder="Transactions of one account, newest first"
            value={endpoint.description}
            onChange={(e) => onChange({ ...endpoint, description: e.target.value })}
          />
        </FormField>
        {endpoint.params.length > 0 && (
          <Stack gap="--space-2">
            <Text size="xs" tone="faint">
              Parameters
            </Text>
            {endpoint.params.map((param, i) => (
              <ParamRow
                key={i}
                id={`${id}-p${i}`}
                param={param}
                onChange={(next) => setParam(i, next)}
                onRemove={() =>
                  onChange({ ...endpoint, params: endpoint.params.filter((_, j) => j !== i) })
                }
              />
            ))}
          </Stack>
        )}
        <Cluster justify="between">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => onChange({ ...endpoint, params: [...endpoint.params, newParam()] })}
          >
            Add a parameter
          </Button>
          <Button size="sm" onClick={onDone}>
            Done
          </Button>
        </Cluster>
      </Stack>
    </div>
  )
}

function summaryOf(endpoint: RestEndpoint): string {
  const count = endpoint.params.length
  const params = count ? `${count} parameter${count === 1 ? '' : 's'}` : ''
  return [endpoint.description, params].filter(Boolean).join(' · ')
}

export interface RestEndpointsEditorProps {
  endpoints: RestEndpoint[]
  onChange: (next: RestEndpoint[]) => void
}

export function RestEndpointsEditor({ endpoints, onChange }: RestEndpointsEditorProps) {
  const [open, setOpen] = useState<number | null>(null)

  const replace = (i: number, next: RestEndpoint) =>
    onChange(endpoints.map((e, j) => (j === i ? next : e)))
  const remove = (i: number) => {
    onChange(endpoints.filter((_, j) => j !== i))
    setOpen(null)
  }
  const add = () => {
    onChange([...endpoints, newEndpoint()])
    setOpen(endpoints.length)
  }

  return (
    <Stack gap="--space-3">
      {endpoints.length > 0 && (
        <div className="conn-acct-group" role="list" aria-label="Endpoints">
          {endpoints.map((endpoint, i) =>
            open === i ? (
              <div key={i} role="listitem" className="rest-ep-open">
                <EndpointForm
                  id={`rest-ep-${i}`}
                  endpoint={endpoint}
                  onChange={(next) => replace(i, next)}
                  onDone={() => setOpen(null)}
                />
              </div>
            ) : (
              <div key={i} role="listitem" className="conn-acct-row">
                <span className={`rest-method rest-method--${endpoint.method.toLowerCase()}`}>
                  {endpoint.method}
                </span>
                <div className="conn-acct-main">
                  <div className="conn-acct-name rest-path" title={endpoint.path}>
                    {endpoint.path || 'New endpoint'}
                  </div>
                  {summaryOf(endpoint) && (
                    <div className="conn-acct-sub">{summaryOf(endpoint)}</div>
                  )}
                </div>
                <div className="conn-acct-actions">
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Edit ${endpoint.method} ${endpoint.path}`}
                    onClick={() => setOpen(i)}
                  >
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Remove ${endpoint.method} ${endpoint.path}`}
                    onClick={() => remove(i)}
                  >
                    Remove
                  </Button>
                </div>
              </div>
            ),
          )}
        </div>
      )}
      <div>
        <Button size="sm" onClick={add}>
          Add an endpoint
        </Button>
      </div>
    </Stack>
  )
}
