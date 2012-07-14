#!/usr/bin/env python
'''
Created on Jun 22, 2012

@author: Yangming
'''
import datetime
import re
import commands
import nagios
import statsd
from nagios import CommandBasedPlugin as plugin

class MongoDBChecker(nagios.BatchStatusPlugin):
    def __init__(self, *args, **kwargs):
        super(MongoDBChecker, self).__init__(*args, **kwargs)
        self.parser.add_argument("-f", "--filename", default='mongo', type=str, required=False)
        self.parser.add_argument("-u", "--user", required=False, type=str)
        self.parser.add_argument("-s", "--password", required=False, type=str)
        self.parser.add_argument("-H", "--host", required=False, type=str)
        self.parser.add_argument("-p", "--port", required=False, type=int)

    def _get_batch_status(self, request):
        cmd = "mongostat -n 1 --noheaders"
        return commands.getoutput(cmd)

    def _parse_output(self, request, output):
        fields = output.split('\n')[1].strip().split()
        headers = ["insert",     "query",   "update",  "delete",
                   "getmore",    "command", "flushes", "mapped",
                   "vsize",      "res",     "faults",  "locked %",
                   "idx miss %", "qr|qw",   "ar|aw",   "netIn",
                   "netOut",     "conn",    "time"]
        for k, v in zip(headers, fields):
            if k == "time":
                uptime = [int(t) for t in v.split(":")]
                if len(uptime) != 3:
                    raise nagios.OutputFormatError(request, output)
                sec = datetime.timedelta(hours=uptime[0],
                                         minutes=uptime[1],
                                         seconds=uptime[2]).total_seconds()
                yield k, sec
            elif "|" in k:
                for k, v in zip(k.split("|"), v.split("|")):
                    value = nagios.to_num(v)
                    if value:
                        yield k, value
            else:
                pattern = re.compile('(\d+)[mkb]?')
                matchResult = pattern.match(v)
                if not matchResult:
                    raise nagios.OutputFormatError(request, output)
                value = int(matchResult.groups(1)[0])
                yield k, value

    def run_query(self, request, query):
        output = self._get_query_status(request, query)
        self._validate_output(request, output)
        return output

    def _get_query_status(self, request, query):
        query_template = "mongo --quiet" 
        if hasattr(request, "user") and request.user is not None:
            query_template += " -u %s " % request.user + query_template
        if hasattr(request, "password") and request.password is not None:
            query_template += " -p %s" % request.password
        if hasattr(request, "host") and request.host is not None:
            query_template += " --host %s " % request.host + query_template
        if hasattr(request, "port") and request.host is not None:
            query_template += " --port %s" % request.port
        query_template += " --eval \'%s\'"
        query = query_template % query
        return commands.getoutput(query)

    def _validate_output(self, request, output):
        if "command not found" in output:
            raise nagios.ServiceInaccessibleError(request, output)
        elif "Error: couldn't connect to server" in output:
            raise nagios.ServiceInaccessibleError(request, output)
        elif "exception: login failed" in output:
            raise nagios.AuthenticationFailedError(request, output)
        elif "ERROR" in output:
            raise nagios.StatusUnknownError(request, output)
        elif output.strip() == "":
            raise nagios.StatusUnknownError(request)
        return True

    @plugin.command("CONNECTIONS")
    @statsd.gauge("sys.app.mongodb.connections")
    def get_connections(self, request):
        query = "db.serverStatus().connections.current"
        value = nagios.to_num(self.run_query(request, query))
        return self.get_result(request, value, '%s new connections' % value, 'conns')

    @plugin.command("MEMORY_USED")
    @statsd.gauge("sys.app.mongodb.memory_used")
    def get_memory_used(self, request):
        query = "db.serverStatus().mem.resident"
        value = nagios.to_num(self.run_query(request, query))
        return self.get_result(request, value, '%sMB resident size' % value, 'res', UOM='MB')

    @plugin.command("INSERT")
    @statsd.counter("sys.app.mongodb.insert")
    def get_insert(self, request):
        query = "db.serverStatus().opcounters.insert"
        value = nagios.to_num(self.run_query(request, query))
        return self.get_result(request, value, '%s inserts' % value, 'inserts')

    @plugin.command("UPDATE")
    @statsd.counter("sys.app.mongodb.update")
    def get_update(self, request):
        query = "db.serverStatus().opcounters.update"
        value = nagios.to_num(self.run_query(request, query))
        return self.get_result(request, value, '%s updates' % value, 'updates')

    @plugin.command("COMMAND")
    @statsd.counter("sys.app.mongodb.command")
    def get_command(self, request):
        query = "db.serverStatus().opcounters.command"
        value = nagios.to_num(self.run_query(request, query))
        return self.get_result(request, value, '%s commands' % value, 'commands')

    @plugin.command("QUERY")
    @statsd.counter("sys.app.mongodb.query")
    def get_query(self, request):
        query = "db.serverStatus().opcounters.query"
        value = nagios.to_num(self.run_query(request, query))
        return self.get_result(request, value, '%s queries' % value, 'queries')

    @plugin.command("DELETE")
    @statsd.counter("sys.app.mongodb.delete")
    def get_delete_rate(self, request):
        query = "db.serverStatus().opcounters.delete"
        value = nagios.to_num(self.run_query(request, query))
        return self.get_result(request, value, '%s deletes' % value, 'deletes')

    @plugin.command("LOCKED_PERCENTAGE")
    @statsd.gauge("sys.app.mongodb.locked_ratio")
    def get_locked_ratio(self, request):
        value = self.get_status_value("locked %", request)
        return self.get_result(request, value, str(value) + '% locked', 'ratio', UOM="%")

    @plugin.command("MISS_RATIO")
    @statsd.gauge("sys.app.mongodb.miss_ratio")
    def get_miss_ratio(self, request):
        query = "db.serverStatus().indexCounters.btree.missRatio"
        value = nagios.to_num(self.run_query(request, query))
        return self.get_result(request, value, str(value) + '% missed', 'missed', UOM="%")

    @plugin.command("RESETS")
    @statsd.counter("sys.app.mongodb.resets")
    def get_resets(self, request):
        query = "db.serverStatus().indexCounters.btree.resets"
        value = nagios.to_num(self.run_query(request, query))
        return self.get_result(request, value, str(value) + 'resets', 'resets')

    @plugin.command("HITS")
    @statsd.counter("sys.app.mongodb.hits")
    def get_hits(self, request):
        query = "db.serverStatus().indexCounters.btree.hits"
        value = nagios.to_num(self.run_query(request, query))
        return self.get_result(request, value, str(value) + 'hits', 'hits')

    @plugin.command("MISSES")
    @statsd.counter("sys.app.mongodb.misses")
    def get_misses(self, request):
        query = "db.serverStatus().indexCounters.btree.misses"
        value = nagios.to_num(self.run_query(request, query))
        return self.get_result(request, value, str(value) + 'misses', 'misses')

    @plugin.command("ACCESSES")
    @statsd.counter("sys.app.mongodb.accesses")
    def get_accesses(self, request):
        query = "db.serverStatus().indexCounters.btree.accesses"
        value = nagios.to_num(self.run_query(request, query))
        return self.get_result(request, value, str(value) + 'accesses', 'accesses')

if __name__ == "__main__":
    import sys
    MongoDBChecker().run(sys.argv[1:])