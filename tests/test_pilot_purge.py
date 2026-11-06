from guideos.pilot.purge_data import purge_pilot_data


class FakeConnection:
    def __init__(self):
        self.statements = []

    def execute(self, statement, *args):
        self.statements.append(" ".join(statement.split()))


def test_purge_removes_conversations_and_sessions():
    connection = FakeConnection()

    purge_pilot_data(connection)

    assert connection.statements == [
        "DELETE FROM conversations",
        "DELETE FROM profiles",
        "DELETE FROM assessment_sessions",
    ]
