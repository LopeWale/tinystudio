"""Durable lease-scoped termination; host release is no physical-state claim."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import uuid


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def _error(message):
    from .gateway import GatewayError
    return GatewayError(message)


class GatewayLeaseLifecycle:
    """One journal boundary shared by release, observed expiry, and cancellation.

    The gateway owns the transaction and send locks. No transport operation takes
    place while recording a revocation. An action is claimed durably before its
    command is admitted, and the final-write check also verifies this intent.
    """
    def __init__(self,gateway):self.gateway=gateway

    def install(self):
        self.gateway._db.executescript('''
            CREATE TABLE IF NOT EXISTS lease_events (
                event_key TEXT PRIMARY KEY, event_id TEXT NOT NULL, request TEXT NOT NULL, owner TEXT NOT NULL,
                lease_epoch INTEGER NOT NULL, lease_expires REAL NOT NULL,
                trigger TEXT NOT NULL, action TEXT NOT NULL, observed_at REAL NOT NULL,
                revocation_epoch INTEGER NOT NULL, state TEXT NOT NULL,
                stop_command_id TEXT, receipt TEXT, error TEXT,
                UNIQUE(trigger,event_id));
            CREATE INDEX IF NOT EXISTS lease_events_owner ON lease_events(owner,lease_epoch);
        ''')

    def recover(self):
        # An executing action may have written before the process disappeared.
        # Pending means the durable claim was never made and no write was allowed.
        for event in self.gateway._db.execute("SELECT * FROM lease_events WHERE state='executing'").fetchall():
            self.finish(event,'unknown',error='gateway_restarted_during_action')

    def aborted(self,owner,epoch):
        gateway=self.gateway
        with gateway._mutex:
            if gateway._closed:return True
            return gateway._db.execute('SELECT 1 FROM lease_events WHERE owner=? AND lease_epoch=? LIMIT 1',
                                       (owner,epoch)).fetchone() is not None

    def end(self,lease,*,now,trigger,event_id=None,already_revoked=False):
        gateway=self.gateway
        identity={'endpoint_id':gateway._binding['endpoint_id'],'owner':lease['owner'],
                  'lease_epoch':lease['epoch'],'lease_expires':lease['lease_expires'],'trigger':trigger}
        if event_id is None:event_id=trigger+':'+hashlib.sha256(_json(identity).encode()).hexdigest()
        # Separate caller-selected release IDs from generated expiry/cancel IDs.
        # Structural encoding permits every authored string without a reserved
        # prefix or an ambiguous delimiter namespace.
        event_key=_json([trigger,event_id])
        action=getattr(gateway.lease_policy,'on_'+trigger)
        state=gateway._db.execute('SELECT * FROM gateway WHERE id=1').fetchone()
        if not already_revoked and (state['state']!='owned' or state['owner']!=lease['owner'] or state['epoch']!=lease['epoch']):
            raise _error('lease termination requires the exact current owner and epoch')
        revocation_epoch=state['epoch'] if already_revoked else state['epoch']+1
        request={**identity,'event_id':event_id,'event_key':event_key,'action':action,'observed_at':now}
        gateway._db.execute("INSERT INTO lease_events VALUES(?,?,?,?,?,?,?,?,?,?,'pending',NULL,NULL,NULL)",
            (event_key,event_id,_json(request),lease['owner'],lease['epoch'],lease['lease_expires'],trigger,action,now,revocation_epoch))
        if not already_revoked:gateway._recovery('lease_expired' if trigger=='expiry' else 'lease_'+trigger)
        return event_key

    def release(self,event_id,*,owner,epoch):
        gateway=self.gateway
        gateway._identifier(event_id,'lease event')
        if type(epoch) is not int or not isinstance(owner,str):raise _error('release requires an owner and integer epoch')
        with gateway._transaction():
            existing=gateway._db.execute("SELECT * FROM lease_events WHERE trigger='release' AND event_id=?",(event_id,)).fetchone()
            if existing is not None:
                if existing['trigger']!='release' or existing['owner']!=owner or existing['lease_epoch']!=epoch:
                    raise _error('lease event identity belongs to a different immutable request')
                if existing['state'] in {'completed','superseded'}:return json.loads(existing['receipt'])
                raise _error('lease action is pending or uncertain; inspect retained evidence, never replay automatically')
            now,lease=gateway._authority(owner,epoch)
            event_key=self.end(lease,now=now,trigger='release',event_id=event_id)
        return self.run(event_key)

    def cancel(self,request,lease):
        gateway=self.gateway
        if gateway._stop_requested.is_set() or self.aborted(request['owner'],request['epoch']):return None
        event_id='cancel:'+hashlib.sha256(request['command_id'].encode()).hexdigest()
        with gateway._transaction():
            now=gateway._now()
            if self.aborted(request['owner'],request['epoch']):return None
            state=gateway._db.execute('SELECT * FROM gateway WHERE id=1').fetchone()
            if state['state']=='owned' and state['owner']==lease['owner'] and state['epoch']==lease['epoch']:
                event_key=self.end(state,now=now,trigger='cancel',event_id=event_id)
            elif state['state']=='recovery_required' and state['epoch']==lease['epoch']+1:
                # The same in-flight command may already have recorded an unknown
                # outcome. Reuse that revocation, rather than breaking its fence.
                event_key=self.end(lease,now=now,trigger='cancel',event_id=event_id,already_revoked=True)
            else:return None
        return self.run(event_key)

    def current(self,event):
        state=self.gateway._db.execute('SELECT * FROM gateway WHERE id=1').fetchone()
        return state['state']=='recovery_required' and state['epoch']==event['revocation_epoch']

    def finish(self,event,state,*,delivery=None,evidence=None,error=None):
        gateway=self.gateway
        current=self.current(event)
        if state=='unknown':
            # A completion commit can become visible before its acknowledgement
            # is lost. If that receipt cannot be confirmed by read-back, retain
            # recovery for this generation without ever revoking a newer owner.
            gateway._db.execute("UPDATE gateway SET state='recovery_required',reason='uncertain_lease_action' WHERE id=1 AND state='idle' AND epoch=?",
                                (event['revocation_epoch'],))
        if state=='completed' and event['trigger']=='release' and current:
            uncertain=gateway._db.execute("SELECT 1 FROM commands WHERE state IN ('pending','unknown') LIMIT 1").fetchone()
            other=gateway._db.execute("SELECT 1 FROM lease_events WHERE event_key!=? AND state IN ('pending','executing','unknown') LIMIT 1",
                                      (event['event_key'],)).fetchone()
            if uncertain is None and other is None:
                gateway._db.execute("UPDATE gateway SET state='idle',owner=NULL,lease_expires=0,reason=NULL WHERE id=1")
        host=gateway._db.execute('SELECT state FROM gateway WHERE id=1').fetchone()['state']
        receipt={'format':'tinystudio.gateway-lease-event.v1',**json.loads(event['request']),
            'revocation_epoch':event['revocation_epoch'],'state':state,'host_state':host,
            'physical_outcome':'unverified','device_application_confirmed':False,
            'host_authority_revoked':True,'stop_command_id':event['stop_command_id'],
            'disclosure':'Host lease/action evidence; physical stopping and exactly-once effects are unverified.'}
        if delivery is not None:receipt['delivery_receipt']=deepcopy(delivery)
        if evidence is not None:receipt['delivery_evidence']=deepcopy(evidence)
        if error is not None:receipt['error_type']=error
        if event['receipt'] is not None:receipt['prior_lifecycle_receipt']=json.loads(event['receipt'])
        gateway._db.execute('UPDATE lease_events SET state=?,receipt=?,error=? WHERE event_key=?',
                            (state,_json(receipt),error,event['event_key']))
        return receipt

    def run(self,event_key):
        gateway=self.gateway
        with gateway._send_lock:
            with gateway._transaction():
                now=gateway._now()
                event=gateway._db.execute('SELECT * FROM lease_events WHERE event_key=?',(event_key,)).fetchone()
                if event is None:raise _error('unknown lease event')
                if event['state'] in {'completed','superseded'}:return json.loads(event['receipt'])
                if event['state']!='pending':raise _error('lease action is uncertain; explicit reconciliation required')
                if not self.current(event):return self.finish(event,'superseded',error='revocation_fence_superseded')
                gateway._db.execute("UPDATE lease_events SET state='executing' WHERE event_key=?",(event_key,))
            # A second transaction keeps the action claim durable even if command
            # construction/admission fails. No transport call precedes this claim.
            request=None;delivery=None
            try:
                with gateway._transaction():
                    now=gateway._now()
                    event=gateway._db.execute('SELECT * FROM lease_events WHERE event_key=?',(event_key,)).fetchone()
                    if not self.current(event):return self.finish(event,'superseded',error='revocation_fence_superseded')
                    if event['action']=='revoke_only':return self.finish(event,'completed')
                    # Author-selected IDs share this journal. Allocate under the
                    # admission transaction and retain the exact chosen identity.
                    while True:
                        command_id='lease-stop:'+uuid.uuid4().hex
                        if gateway._db.execute('SELECT 1 FROM commands WHERE command_id=?',(command_id,)).fetchone() is None:
                            break
                    gateway._db.execute('UPDATE lease_events SET stop_command_id=? WHERE event_key=?',(command_id,event_key))
                    request=gateway._stop_request(command_id,event['revocation_epoch'],now)
                    gateway._pending(request)
                    if gateway._native_profile is not None and gateway._native_profile.local_stop_payload is None:
                        delivery=gateway._record_unconfigured_stop(request)
                        event=gateway._db.execute('SELECT * FROM lease_events WHERE event_key=?',(event_key,)).fetchone()
                        return self.finish(event,'completed',delivery=delivery)
                delivery=gateway._effect(request,lambda:False)
                with gateway._transaction():
                    gateway._now()
                    event=gateway._db.execute('SELECT * FROM lease_events WHERE event_key=?',(event_key,)).fetchone()
                    return self.finish(event,'completed',delivery=delivery)
            except Exception as cause:
                try:
                    completed=None
                    with gateway._transaction():
                        event=gateway._db.execute('SELECT * FROM lease_events WHERE event_key=?',(event_key,)).fetchone()
                        if event['state']=='completed' and event['receipt'] is not None:
                            completed=json.loads(event['receipt'])
                    # A successful fresh transaction confirms a completed receipt
                    # even if its original COMMIT acknowledgement was lost.
                    if completed is not None:return completed
                except Exception as readback:
                    cause.add_note('Lease completion read-back unconfirmed: '+type(readback).__name__)
                try:
                    with gateway._transaction():
                        event=gateway._db.execute('SELECT * FROM lease_events WHERE event_key=?',(event_key,)).fetchone()
                        evidence=getattr(cause,'delivery_receipt',None)
                        if delivery is not None:
                            evidence={'classification':'lease_receipt_commit_unconfirmed',
                                      'observed_delivery':delivery,'physical_outcome':'unverified'}
                        receipt=self.finish(event,'unknown',evidence=evidence,error=type(cause).__name__)
                    cause.lifecycle_receipt=receipt
                except Exception as secondary:
                    cause.add_note('Lease action journal update unconfirmed: '+type(secondary).__name__)
                raise

    def tick(self):
        gateway=self.gateway
        with gateway._transaction():
            gateway._now()
            identities=[row[0] for row in gateway._db.execute("SELECT event_key FROM lease_events WHERE state='pending' ORDER BY rowid")]
        return [self.run(identity) for identity in identities]

    def supersede(self):
        events=self.gateway._db.execute("SELECT * FROM lease_events WHERE state IN ('pending','executing','unknown')").fetchall()
        for event in events:self.finish(event,'superseded',error='operator_reconciliation')
        return [event['event_key'] for event in events]

    def events(self):
        with self.gateway._transaction():
            rows=[dict(row) for row in self.gateway._db.execute('SELECT * FROM lease_events ORDER BY rowid')]
        for row in rows:
            row['request']=json.loads(row['request'])
            row['receipt']=json.loads(row['receipt']) if row['receipt'] is not None else None
        return rows
