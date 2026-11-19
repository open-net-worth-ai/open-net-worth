import { Checkbox } from '../../ui'
import { Trash2 } from '../../../ui/icons'
import { Icons } from './icons'
import { Btn } from './primitives'
import { RestField } from './restFormParts'
import { REST_METHODS } from './restApi'
import type { RestEndpoint, RestMethod, RestParam, RestParamPlace } from './restApi'

// One endpoint of a REST API, open for editing inside the endpoint list: its method and path,
// what it is for, and its parameters (which become the tool's arguments). In the connectors
// screen's own form language, like the rest of the form (./restFormParts).

const PLACES: { value: RestParamPlace; label: string }[] = [
  { value: 'query', label: 'Query' },
  { value: 'path', label: 'Path' },
  { value: 'body', label: 'Body' },
]

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
      <input
        className="rd-input"
        aria-label="Parameter name"
        placeholder="name"
        value={param.name}
        onChange={(e) => onChange({ ...param, name: e.target.value })}
      />
      <select
        className="rd-select"
        aria-label="Where the parameter goes"
        value={param.in}
        onChange={(e) => onChange({ ...param, in: e.target.value as RestParamPlace })}
      >
        {PLACES.map((p) => (
          <option key={p.value} value={p.value}>
            {p.label}
          </option>
        ))}
      </select>
      <input
        className="rd-input"
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
      <button
        type="button"
        className="rest-ep-icon rest-ep-icon--remove"
        title="Remove"
        aria-label="Remove parameter"
        onClick={onRemove}
      >
        <Trash2 size={15} aria-hidden />
      </button>
    </div>
  )
}

interface EndpointFormProps {
  id: string
  endpoint: RestEndpoint
  onChange: (next: RestEndpoint) => void
  /** Take this endpoint off the list (the list asks first). */
  onRemove: () => void
  onDone: () => void
}

export function EndpointForm({ id, endpoint, onChange, onRemove, onDone }: EndpointFormProps) {
  const setParam = (i: number, next: RestParam) =>
    onChange({ ...endpoint, params: endpoint.params.map((p, j) => (j === i ? next : p)) })
  return (
    <div className="rest-ep-form">
      <div className="rest-ep-line">
        <select
          className={`rd-select rest-method-select rest-method--${endpoint.method.toLowerCase()}`}
          aria-label="Method"
          value={endpoint.method}
          onChange={(e) => onChange({ ...endpoint, method: e.target.value as RestMethod })}
        >
          {REST_METHODS.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <input
          className="rd-input rest-path"
          aria-label="Path"
          placeholder="/accounts/{account_id}/transactions"
          value={endpoint.path}
          autoFocus={!endpoint.path}
          onChange={(e) => onChange({ ...endpoint, path: e.target.value })}
        />
      </div>
      <RestField
        label="Description"
        htmlFor={`${id}-desc`}
        full={false}
        help="What it returns or does. Lucius reads this to decide when to call it."
      >
        <input
          id={`${id}-desc`}
          className="rd-input"
          placeholder="Transactions of one account, newest first"
          value={endpoint.description}
          onChange={(e) => onChange({ ...endpoint, description: e.target.value })}
        />
      </RestField>
      {endpoint.params.length > 0 && (
        <div className="rd-field">
          <span className="rd-label">Parameters</span>
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
        </div>
      )}
      <div className="rest-inline rest-inline--apart">
        <Btn
          variant="ghost"
          onClick={() => onChange({ ...endpoint, params: [...endpoint.params, newParam()] })}
        >
          <Icons.plus /> Add a parameter
        </Btn>
        <span className="rest-inline">
          <Btn variant="ghost" onClick={onRemove}>
            Remove endpoint
          </Btn>
          <Btn onClick={onDone}>Done</Btn>
        </span>
      </div>
    </div>
  )
}
