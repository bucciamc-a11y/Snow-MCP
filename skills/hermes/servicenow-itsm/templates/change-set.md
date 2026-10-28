# Proposed Change Set (copy and fill)

```
PROPOSED CHANGE SET   plan_id: PLN-YYYYMMDD-NNN
Target:      <table> <INC/CHG/PRB number>   (sys_id: <32 hex>)
Read at:     <sys_updated_on or "version not returned by tool">   (aborts if the record changes first)
Action:      <tool id>   Server preview: <ok | not available>

Field              Current              Proposed
-----------------  -------------------  -------------------
<field>            <value>              <value>

Rationale:   <one line per field>
Risk:        <what goes wrong if this is wrong; side effects such as SLA clocks, paging, notifications>
Reversible:  <how to undo; or "no" and why>
Visibility:  internal work note only   |   **CUSTOMER-VISIBLE: <field>**

Reply  APPROVE PLN-YYYYMMDD-NNN  to execute, REJECT PLN-YYYYMMDD-NNN to discard,
or tell me what to change. Expires <HH:MM> <tz> (30 min).
```

Resolve/close add: `Also type the ticket number to confirm: INC0000000.`

Batch: one block per plan_id; end with `Approve each plan_id explicitly, for example: APPROVE PLN-…-001 PLN-…-002`.
