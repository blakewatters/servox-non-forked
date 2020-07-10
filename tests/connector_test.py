import json
import os
from pathlib import Path

import pytest
import typer
import yaml
from typer.testing import CliRunner

from connectors.vegeta.vegeta import TargetFormat, VegetaConnector, VegetaSettings
from pydantic import ValidationError
from servo.connector import (
    Connector,
    ConnectorSettings,
    EventResult,
    License,
    Maturity,
    Optimizer,
    Version,
    event,
)
from servo.servo import BaseServoSettings, ServoAssembly
from servo.cli import ConnectorCLI, ServoCLI
from tests.test_helpers import environment_overrides


class TestOptimizer:
    def test_org_domain_valid(self) -> None:
        optimizer = Optimizer(id="example.com/my-app", token="123456")
        assert optimizer.org_domain == "example.com"

    def test_org_domain_invalid(self) -> None:
        with pytest.raises(ValidationError) as e:
            Optimizer(id="invalid/my-app", token="123456")
        assert "1 validation error for Optimizer" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("org_domain",)
        assert (
            e.value.errors()[0]["msg"]
            == 'string does not match regex "(([\\da-zA-Z])([_\\w-]{,62})\\.){,127}(([\\da-zA-Z])[_\\w-]{,61})?([\\da-zA-Z]\\.((xn\\-\\-[a-zA-Z\\d]+)|([a-zA-Z\\d]{2,})))"'
        )

    def test_app_name_valid(self) -> None:
        optimizer = Optimizer(id="example.com/my-app", token="123456")
        assert optimizer.app_name == "my-app"

    def test_app_name_invalid(self) -> None:
        with pytest.raises(ValidationError) as e:
            Optimizer(id="example.com/$$$invalid$$$", token="123456")
        assert "1 validation error for Optimizer" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("app_name",)
        assert (
            e.value.errors()[0]["msg"]
            == 'string does not match regex "^[a-z\\-]{3,64}$"'
        )

    def test_token_validation(self) -> None:
        with pytest.raises(ValidationError) as e:
            Optimizer(id="example.com/my-app", token=None)
        assert "1 validation error for Optimizer" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("token",)
        assert e.value.errors()[0]["msg"] == "none is not an allowed value"

    def test_base_url_validation(self) -> None:
        with pytest.raises(ValidationError) as e:
            Optimizer(id="example.com/my-app", token="123456", base_url="INVALID")
        assert "1 validation error for Optimizer" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("base_url",)
        assert e.value.errors()[0]["msg"] == "invalid or missing URL scheme"


class TestLicense:
    def test_license_from_string(self):
        l = License.from_str("MIT")
        assert l == License.MIT

    def test_license_from_string_invalid_raises(self):
        with pytest.raises(NameError) as e:
            License.from_str("INVALID")
        assert 'No license identified by "INVALID"' in str(e)


class TestMaturity:
    def test_maturity_from_string(self):
        l = Maturity.from_str("Stable")
        assert l == Maturity.STABLE

    def test_license_from_string_invalid_raises(self):
        with pytest.raises(NameError) as e:
            Maturity.from_str("INVALID")
        assert 'No maturity level identified by "INVALID"' in str(e)


class TestConnector:
    def test_subclass_registration(self) -> None:
        class RegisterMe(Connector):
            pass

        assert RegisterMe in Connector.all()

    def test_default_name(self) -> None:
        class TestConnector(Connector):
            pass

        assert TestConnector.name == "Test Connector"

    def test_default_version(self) -> None:
        class TestConnector(Connector):
            pass

        assert TestConnector.version == "0.0.0"

    def test_default_key_path(self) -> None:
        class FancyConnector(Connector):
            pass

        c = FancyConnector(settings=ConnectorSettings())
        assert c.config_key_path == "fancy"


class TestSettings:
    def test_configuring_with_environment_variables(self) -> None:
        assert ConnectorSettings.__fields__["description"].field_info.extra[
            "env_names"
        ] == {"DESCRIPTION"}
        with environment_overrides({"DESCRIPTION": "this description"}):
            assert os.environ["DESCRIPTION"] == "this description"
            s = ConnectorSettings()
            assert s.description == "this description"


class TestServoSettings:
    def test_ignores_extra_attributes(self) -> None:
        # Ignored attribute would raise if misconfigured
        s = BaseServoSettings(
            ignored=[], optimizer=Optimizer(id="example.com/my-app", token="123456")
        )
        with pytest.raises(AttributeError) as e:
            s.ignored
        assert "'BaseServoSettings' object has no attribute 'ignored'" in str(e)

    def test_override_optimizer_settings_with_env_vars(self) -> None:
        with environment_overrides({"OPSANI_TOKEN": "abcdefg"}):
            assert os.environ["OPSANI_TOKEN"] is not None
            optimizer = Optimizer(app_name="foo", org_domain="dsada.com")
            assert optimizer.token == "abcdefg"

    def test_set_connectors_with_env_vars(self) -> None:
        with environment_overrides({"SERVO_CONNECTORS": '["measure"]'}):
            assert os.environ["SERVO_CONNECTORS"] is not None
            s = BaseServoSettings(
                optimizer={
                    "app_name": "my-app",
                    "org_domain": "example.com",
                    "token": "123456789",
                }
            )
            assert s is not None
            schema = s.schema()
            assert schema["properties"]["connectors"]["env_names"] == {
                "SERVO_CONNECTORS"
            }
            assert s.connectors is not None
            assert s.connectors == ["measure"]

    def test_connectors_allows_none(self):
        s = BaseServoSettings(
            optimizer={
                "app_name": "my-app",
                "org_domain": "example.com",
                "token": "123456789",
            },
            connectors=None,
        )
        assert s.connectors is None

    def test_connectors_allows_set_of_classes(self):
        class FooConnector(Connector):
            pass

        class BarConnector(Connector):
            pass

        s = BaseServoSettings(
            optimizer={
                "app_name": "my-app",
                "org_domain": "example.com",
                "token": "123456789",
            },
            connectors={FooConnector, BarConnector},
        )
        assert set(s.connectors) == {'FooConnector', 'BarConnector'}

    def test_connectors_rejects_invalid_connector_set_elements(self):
        with pytest.raises(ValidationError) as e:
            BaseServoSettings(
                optimizer={
                    "app_name": "my-app",
                    "org_domain": "example.com",
                    "token": "123456789",
                },
                connectors={BaseServoSettings},
            )
        assert "1 validation error for BaseServoSettings" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("connectors",)
        assert (
            e.value.errors()[0]["msg"]
            == "Invalid connectors value: <class 'servo.servo.BaseServoSettings'>"
        )

    def test_connectors_allows_set_of_class_names(self):
        s = BaseServoSettings(
            optimizer={
                "app_name": "my-app",
                "org_domain": "example.com",
                "token": "123456789",
            },
            connectors={"MeasureConnector", "AdjustConnector"},
        )
        assert set(s.connectors) == {"MeasureConnector", "AdjustConnector"}

    def test_connectors_rejects_invalid_connector_set_class_name_elements(self):
        with pytest.raises(ValidationError) as e:
            BaseServoSettings(
                optimizer={
                    "app_name": "my-app",
                    "org_domain": "example.com",
                    "token": "123456789",
                },
                connectors={"BaseServoSettings"},
            )
        assert "1 validation error for BaseServoSettings" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("connectors",)
        assert (
            e.value.errors()[0]["msg"]
            == "BaseServoSettings is not a Connector subclass"
        )

    def test_connectors_allows_set_of_keys(self):
        s = BaseServoSettings(
            optimizer={
                "app_name": "my-app",
                "org_domain": "example.com",
                "token": "123456789",
            },
            connectors={"vegeta"},
        )
        assert s.connectors == ["vegeta"]

    def test_connectors_allows_dict_of_keys_to_classes(self):
        s = BaseServoSettings(
            optimizer={
                "app_name": "my-app",
                "org_domain": "example.com",
                "token": "123456789",
            },
            connectors={"alias": VegetaConnector},
        )
        assert s.connectors == {"alias": 'VegetaConnector'}

    def test_connectors_allows_dict_of_keys_to_class_names(self):
        s = BaseServoSettings(
            optimizer={
                "app_name": "my-app",
                "org_domain": "example.com",
                "token": "123456789",
            },
            connectors={"alias": "VegetaConnector"},
        )
        assert s.connectors == {"alias": "VegetaConnector"}

    def test_connectors_allows_dict_with_explicit_map_to_default_key_path(self):
        s = BaseServoSettings(
            optimizer={
                "app_name": "my-app",
                "org_domain": "example.com",
                "token": "123456789",
            },
            connectors={"vegeta": "VegetaConnector"},
        )
        assert s.connectors == {"vegeta": "VegetaConnector"}

    def test_connectors_allows_dict_with_explicit_map_to_default_class(self):
        s = BaseServoSettings(
            optimizer={
                "app_name": "my-app",
                "org_domain": "example.com",
                "token": "123456789",
            },
            connectors={"vegeta": VegetaConnector},
        )
        assert s.connectors == {"vegeta": 'VegetaConnector'}

    def test_connectors_forbids_dict_with_existing_key(self):
        with pytest.raises(ValidationError) as e:
            BaseServoSettings(
                optimizer={
                    "app_name": "my-app",
                    "org_domain": "example.com",
                    "token": "123456789",
                },
                connectors={"vegeta": "MeasureConnector"},
            )
        assert "1 validation error for BaseServoSettings" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("connectors",)
        assert (
            e.value.errors()[0]["msg"]
            == 'Key "vegeta" is reserved by `VegetaConnector`'
        )

    @pytest.fixture(autouse=True, scope="session")
    def discover_connectors(self) -> None:
        from servo.connector import ConnectorLoader

        loader = ConnectorLoader()
        for connector in loader.load():
            pass

    def test_connectors_forbids_dict_with_reserved_key(self):
        with pytest.raises(ValidationError) as e:
            BaseServoSettings(
                optimizer={
                    "app_name": "my-app",
                    "org_domain": "example.com",
                    "token": "123456789",
                },
                connectors={"connectors": "VegetaConnector"},
            )
        assert "1 validation error for BaseServoSettings" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("connectors",)
        assert e.value.errors()[0]["msg"] == 'Key "connectors" is reserved'

    def test_connectors_forbids_dict_with_invalid_key(self):
        with pytest.raises(ValidationError) as e:
            BaseServoSettings(
                optimizer={
                    "app_name": "my-app",
                    "org_domain": "example.com",
                    "token": "123456789",
                },
                connectors={"This Is Not Valid": "VegetaConnector"},
            )
        assert "1 validation error for BaseServoSettings" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("connectors",)
        assert (
            e.value.errors()[0]["msg"]
            == 'Key "This Is Not Valid" is not valid: key paths may only contain alphanumeric characters, hyphens, slashes, periods, and underscores'
        )

    def test_connectors_rejects_invalid_connector_dict_values(self):
        with pytest.raises(ValidationError) as e:
            BaseServoSettings(
                optimizer={
                    "app_name": "my-app",
                    "org_domain": "example.com",
                    "token": "123456789",
                },
                connectors={"whatever": "Not a Real Connector"},
            )
        assert "1 validation error for BaseServoSettings" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("connectors",)
        assert (
            e.value.errors()[0]["msg"]
            == "Invalid connectors value: Not a Real Connector"
        )


class TestServo:
    class FirstTestServoConnector(Connector):
        @event()
        def this_is_an_event(self) -> str:
            return "this is the result"

    class SecondTestServoConnector(Connector):
        @event()
        def this_is_an_event(self) -> str:
            return "this is a different result"

        @event()
        def another_event(self) -> None:
            pass

    def test_init_with_optimizer(self) -> None:
        pass

    def test_all_connectors(self) -> None:
        c = ServoAssembly.construct().all_connectors()
        assert TestServo.FirstTestServoConnector in c

    def test_dispatch_event(self, servo_yaml: Path) -> None:
        config = {
            "connectors": ["first_test_servo", "second_test_servo"],
            "first_test_servo": {},
            "second_test_servo": {},
        }
        servo_yaml.write_text(yaml.dump(config))

        optimizer = Optimizer(id="dev.opsani.com/servox", token="1234556789")

        assembly, servo, DynamicServoSettings = ServoAssembly.assemble(
            config_file=servo_yaml, optimizer=optimizer
        )
        results = servo.dispatch_event("this_is_an_event")
        assert len(results) == 2
        assert results[0].value == "this is the result"

    def test_dispatch_event_first(self, servo_yaml: Path) -> None:
        config = {
            "connectors": ["first_test_servo", "second_test_servo"],
            "first_test_servo": {},
            "second_test_servo": {},
        }
        servo_yaml.write_text(yaml.dump(config))

        optimizer = Optimizer(id="dev.opsani.com/servox", token="1234556789")

        assembly, servo, DynamicServoSettings = ServoAssembly.assemble(
            config_file=servo_yaml, optimizer=optimizer
        )
        result = servo.dispatch_event("this_is_an_event", first=True)
        assert isinstance(result, EventResult)
        assert result.value == "this is the result"

    def test_dispatch_event_include(self, servo_yaml: Path) -> None:
        config = {
            "connectors": ["first_test_servo", "second_test_servo"],
            "first_test_servo": {},
            "second_test_servo": {},
        }
        servo_yaml.write_text(yaml.dump(config))

        optimizer = Optimizer(id="dev.opsani.com/servox", token="1234556789")

        assembly, servo, DynamicServoSettings = ServoAssembly.assemble(
            config_file=servo_yaml, optimizer=optimizer
        )
        first_connector = servo.connectors[0]
        assert first_connector.name == "FirstTestServo Connector"
        results = servo.dispatch_event("this_is_an_event", include=[first_connector])
        assert len(results) == 1
        assert results[0].value == "this is the result"

    def test_dispatch_event_exclude(self, servo_yaml: Path) -> None:
        config = {
            "connectors": ["first_test_servo", "second_test_servo"],
            "first_test_servo": {},
            "second_test_servo": {},
        }
        servo_yaml.write_text(yaml.dump(config))

        optimizer = Optimizer(id="dev.opsani.com/servox", token="1234556789")

        assembly, servo, DynamicServoSettings = ServoAssembly.assemble(
            config_file=servo_yaml, optimizer=optimizer
        )
        assert len(servo.connectors) == 2
        first_connector = servo.connectors[0]
        assert first_connector.name == "FirstTestServo Connector"
        second_connector = servo.connectors[1]
        assert second_connector.name == "SecondTestServo Connector"
        assert second_connector.__events__[
            "this_is_an_event"
        ], "Expected event is not registered"
        results = servo.dispatch_event("this_is_an_event", exclude=[first_connector])
        assert len(results) == 1
        assert results[0].value == "this is a different result"
        assert results[0].connector == second_connector


class TestServoAssembly:
    def test_warning_ambiguous_connectors(self) -> None:
        # TODO: This can be very hard to debug
        # This is where you have 2 connector classes with the same name
        pass

    def test_assemble_assigns_optimizer_to_connectors(self, servo_yaml: Path):
        config = {
            "connectors": {"vegeta": "vegeta"},
            "vegeta": {"duration": 0, "rate": 0, "target": "https://opsani.com/"},
        }
        servo_yaml.write_text(yaml.dump(config))

        optimizer = Optimizer(id="dev.opsani.com/servox", token="1234556789")

        assembly, servo, DynamicServoSettings = ServoAssembly.assemble(
            config_file=servo_yaml, optimizer=optimizer
        )
        connector = servo.connectors[0]
        assert connector.optimizer == optimizer

    def test_aliased_connectors_produce_schema(self, servo_yaml: Path) -> None:
        config = {
            "connectors": {"vegeta": "vegeta", "other": "vegeta"},
            "vegeta": {"duration": 0, "rate": 0, "target": "https://opsani.com/"},
            "other": {"duration": 0, "rate": 0, "target": "https://opsani.com/"},
        }
        servo_yaml.write_text(yaml.dump(config))

        optimizer = Optimizer(id="dev.opsani.com/servox", token="1234556789")

        assembly, servo, DynamicServoSettings = ServoAssembly.assemble(
            config_file=servo_yaml, optimizer=optimizer
        )
        schema = json.loads(DynamicServoSettings.schema_json())

        # Description on parent class can be squirrely
        assert schema["properties"]["description"]["env_names"] == ["SERVO_DESCRIPTION"]
        assert schema == {
            'title': 'Servo Configuration Schema',
            'description': 'Schema for configuration of Servo v0.0.0 with Vegeta Connector v0.5.0',
            'type': 'object',
            'properties': {
                'description': {
                    'title': 'Description',
                    'description': 'An optional annotation describing the configuration.',
                    'env_names': [
                        'SERVO_DESCRIPTION',
                    ],
                    'type': 'string',
                },
                'connectors': {
                    'title': 'Connectors',
                    'description': (
                        'An optional, explicit configuration of the active connectors.\n'
                        '\n'
                        'Configurable as either an array of connector identifiers (names or class) or\n'
                        'a dictionary where the keys specify the key path to the connectors configuration\n'
                        'and the values identify the connector (by name or class name).'
                    ),
                    'examples': [
                        [
                            'kubernetes',
                            'prometheus',
                        ],
                        {
                            'staging_prom': 'prometheus',
                            'gateway_prom': 'prometheus',
                        },
                    ],
                    'env_names': [
                        'SERVO_CONNECTORS',
                    ],
                    'anyOf': [
                        {
                            'type': 'array',
                            'items': {
                                'type': 'string',
                            },
                        },
                        {
                            'type': 'object',
                            'additionalProperties': {
                                'type': 'string',
                            },
                        },
                    ],
                },
                'other': {
                    'title': 'Other',
                    'env_names': [
                        'SERVO_OTHER',
                    ],
                    'allOf': [
                        {
                            '$ref': '#/definitions/VegetaSettings__other',
                        },
                    ],
                },
                'vegeta': {
                    'title': 'Vegeta',
                    'env_names': [
                        'SERVO_VEGETA',
                    ],
                    'allOf': [
                        {
                            '$ref': '#/definitions/VegetaSettings',
                        },
                    ],
                },
            },
            'required': [
                'other',
                'vegeta',
            ],
            'definitions': {
                'VegetaSettings__other': {
                    'title': 'Vegeta Connector Settings (at key-path other)',
                    'description': 'Configuration of the Vegeta connector',
                    'type': 'object',
                    'properties': {
                        'description': {
                            'title': 'Description',
                            'description': 'An optional annotation describing the configuration.',
                            'env_names': [
                                'SERVO_OTHER_DESCRIPTION',
                            ],
                            'type': 'string',
                        },
                        'rate': {
                            'title': 'Rate',
                            'description': (
                                'Specifies the request rate per time unit to issue against the targets. Given in the forma'
                                't of request/time unit.'
                            ),
                            'env_names': [
                                'SERVO_OTHER_RATE',
                            ],
                            'type': 'string',
                        },
                        'duration': {
                            'title': 'Duration',
                            'description': 'Specifies the amount of time to issue requests to the targets.',
                            'env_names': [
                                'SERVO_OTHER_DURATION',
                            ],
                            'type': 'string',
                        },
                        'format': {
                            'title': 'Format',
                            'description': (
                                'Specifies the format of the targets input. Valid values are http and json. Refer to the V'
                                'egeta docs for details.'
                            ),
                            'default': 'http',
                            'env_names': [
                                'SERVO_OTHER_FORMAT',
                            ],
                            'enum': [
                                'http',
                                'json',
                            ],
                            'type': 'string',
                        },
                        'target': {
                            'title': 'Target',
                            'description': (
                                'Specifies a single formatted Vegeta target to load. See the format option to learn about '
                                'available target formats. This option is exclusive of the targets option and will provide'
                                ' a target to Vegeta via stdin.'
                            ),
                            'env_names': [
                                'SERVO_OTHER_TARGET',
                            ],
                            'type': 'string',
                        },
                        'targets': {
                            'title': 'Targets',
                            'description': (
                                'Specifies the file from which to read targets. See the format option to learn about avail'
                                'able target formats. This option is exclusive of the target option and will provide targe'
                                'ts to via through a file on disk.'
                            ),
                            'env_names': [
                                'SERVO_OTHER_TARGETS',
                            ],
                            'type': 'string',
                            'format': 'file-path',
                        },
                        'connections': {
                            'title': 'Connections',
                            'description': 'Specifies the maximum number of idle open connections per target host.',
                            'default': 10000,
                            'env_names': [
                                'SERVO_OTHER_CONNECTIONS',
                            ],
                            'type': 'integer',
                        },
                        'workers': {
                            'title': 'Workers',
                            'description': (
                                'Specifies the initial number of workers used in the attack. The workers will automaticall'
                                'y increase to achieve the target request rate, up to max-workers.'
                            ),
                            'default': 10,
                            'env_names': [
                                'SERVO_OTHER_WORKERS',
                            ],
                            'type': 'integer',
                        },
                        'max_workers': {
                            'title': 'Max Workers',
                            'description': (
                                'The maximum number of workers used to sustain the attack. This can be used to control the'
                                ' concurrency of the attack to simulate a target number of clients.'
                            ),
                            'default': 18446744073709551615,
                            'env_names': [
                                'SERVO_OTHER_MAX_WORKERS',
                            ],
                            'type': 'integer',
                        },
                        'max_body': {
                            'title': 'Max Body',
                            'description': (
                                'Specifies the maximum number of bytes to capture from the body of each response. Remainin'
                                'g unread bytes will be fully read but discarded.'
                            ),
                            'default': -1,
                            'env_names': [
                                'SERVO_OTHER_MAX_BODY',
                            ],
                            'type': 'integer',
                        },
                        'http2': {
                            'title': 'Http2',
                            'description': 'Specifies whether to enable HTTP/2 requests to servers which support it.',
                            'default': True,
                            'env_names': [
                                'SERVO_OTHER_HTTP2',
                            ],
                            'type': 'boolean',
                        },
                        'keepalive': {
                            'title': 'Keepalive',
                            'description': 'Specifies whether to reuse TCP connections between HTTP requests.',
                            'default': True,
                            'env_names': [
                                'SERVO_OTHER_KEEPALIVE',
                            ],
                            'type': 'boolean',
                        },
                        'insecure': {
                            'title': 'Insecure',
                            'description': 'Specifies whether to ignore invalid server TLS certificates.',
                            'default': False,
                            'env_names': [
                                'SERVO_OTHER_INSECURE',
                            ],
                            'type': 'boolean',
                        },
                    },
                    'required': [
                        'rate',
                        'duration',
                    ],
                    'additionalProperties': False,
                },
                'VegetaSettings': {
                    'title': 'Vegeta Connector Settings (at key-path vegeta)',
                    'description': 'Configuration of the Vegeta connector',
                    'type': 'object',
                    'properties': {
                        'description': {
                            'title': 'Description',
                            'description': 'An optional annotation describing the configuration.',
                            'env_names': [
                                'SERVO_VEGETA_DESCRIPTION',
                            ],
                            'type': 'string',
                        },
                        'rate': {
                            'title': 'Rate',
                            'description': (
                                'Specifies the request rate per time unit to issue against the targets. Given in the forma'
                                't of request/time unit.'
                            ),
                            'env_names': [
                                'SERVO_VEGETA_RATE',
                            ],
                            'type': 'string',
                        },
                        'duration': {
                            'title': 'Duration',
                            'description': 'Specifies the amount of time to issue requests to the targets.',
                            'env_names': [
                                'SERVO_VEGETA_DURATION',
                            ],
                            'type': 'string',
                        },
                        'format': {
                            'title': 'Format',
                            'description': (
                                'Specifies the format of the targets input. Valid values are http and json. Refer to the V'
                                'egeta docs for details.'
                            ),
                            'default': 'http',
                            'env_names': [
                                'SERVO_VEGETA_FORMAT',
                            ],
                            'enum': [
                                'http',
                                'json',
                            ],
                            'type': 'string',
                        },
                        'target': {
                            'title': 'Target',
                            'description': (
                                'Specifies a single formatted Vegeta target to load. See the format option to learn about '
                                'available target formats. This option is exclusive of the targets option and will provide'
                                ' a target to Vegeta via stdin.'
                            ),
                            'env_names': [
                                'SERVO_VEGETA_TARGET',
                            ],
                            'type': 'string',
                        },
                        'targets': {
                            'title': 'Targets',
                            'description': (
                                'Specifies the file from which to read targets. See the format option to learn about avail'
                                'able target formats. This option is exclusive of the target option and will provide targe'
                                'ts to via through a file on disk.'
                            ),
                            'env_names': [
                                'SERVO_VEGETA_TARGETS',
                            ],
                            'type': 'string',
                            'format': 'file-path',
                        },
                        'connections': {
                            'title': 'Connections',
                            'description': 'Specifies the maximum number of idle open connections per target host.',
                            'default': 10000,
                            'env_names': [
                                'SERVO_VEGETA_CONNECTIONS',
                            ],
                            'type': 'integer',
                        },
                        'workers': {
                            'title': 'Workers',
                            'description': (
                                'Specifies the initial number of workers used in the attack. The workers will automaticall'
                                'y increase to achieve the target request rate, up to max-workers.'
                            ),
                            'default': 10,
                            'env_names': [
                                'SERVO_VEGETA_WORKERS',
                            ],
                            'type': 'integer',
                        },
                        'max_workers': {
                            'title': 'Max Workers',
                            'description': (
                                'The maximum number of workers used to sustain the attack. This can be used to control the'
                                ' concurrency of the attack to simulate a target number of clients.'
                            ),
                            'default': 18446744073709551615,
                            'env_names': [
                                'SERVO_VEGETA_MAX_WORKERS',
                            ],
                            'type': 'integer',
                        },
                        'max_body': {
                            'title': 'Max Body',
                            'description': (
                                'Specifies the maximum number of bytes to capture from the body of each response. Remainin'
                                'g unread bytes will be fully read but discarded.'
                            ),
                            'default': -1,
                            'env_names': [
                                'SERVO_VEGETA_MAX_BODY',
                            ],
                            'type': 'integer',
                        },
                        'http2': {
                            'title': 'Http2',
                            'description': 'Specifies whether to enable HTTP/2 requests to servers which support it.',
                            'default': True,
                            'env_names': [
                                'SERVO_VEGETA_HTTP2',
                            ],
                            'type': 'boolean',
                        },
                        'keepalive': {
                            'title': 'Keepalive',
                            'description': 'Specifies whether to reuse TCP connections between HTTP requests.',
                            'default': True,
                            'env_names': [
                                'SERVO_VEGETA_KEEPALIVE',
                            ],
                            'type': 'boolean',
                        },
                        'insecure': {
                            'title': 'Insecure',
                            'description': 'Specifies whether to ignore invalid server TLS certificates.',
                            'default': False,
                            'env_names': [
                                'SERVO_VEGETA_INSECURE',
                            ],
                            'type': 'boolean',
                        },
                    },
                    'required': [
                        'rate',
                        'duration',
                    ],
                    'additionalProperties': False,
                },
            },
        }

    def test_aliased_connectors_get_distinct_env_configuration(
        self, servo_yaml: Path
    ) -> None:
        config = {
            "connectors": {"vegeta": "vegeta", "other": "vegeta"},
            "vegeta": {"duration": 0, "rate": 0, "target": "https://opsani.com/"},
            "other": {"duration": 0, "rate": 0, "target": "https://opsani.com/"},
        }
        servo_yaml.write_text(yaml.dump(config))

        optimizer = Optimizer(id="dev.opsani.com/servox", token="1234556789")

        assembly, servo, DynamicServoSettings = ServoAssembly.assemble(
            config_file=servo_yaml, optimizer=optimizer
        )

        # Grab the vegeta field and check it
        vegeta_field = DynamicServoSettings.__fields__["vegeta"]
        vegeta_settings_type = vegeta_field.type_
        assert vegeta_settings_type.__name__ == "VegetaSettings"
        assert vegeta_field.field_info.extra["env_names"] == {"SERVO_VEGETA"}

        # Grab the other field and check it
        other_field = DynamicServoSettings.__fields__["other"]
        other_settings_type = other_field.type_
        assert other_settings_type.__name__ == "VegetaSettings__other"
        assert other_field.field_info.extra["env_names"] == {"SERVO_OTHER"}

        with environment_overrides({"SERVO_DESCRIPTION": "this description"}):
            assert os.environ["SERVO_DESCRIPTION"] == "this description"
            s = DynamicServoSettings(
                other=other_settings_type.construct(),
                vegeta=vegeta_settings_type(
                    rate=10, duration="10s", target="http://example.com/"
                ),
            )
            assert s.description == "this description"

        # Make sure the incorrect case does pass
        with environment_overrides({"SERVO_DURATION": "5m"}):
            with pytest.raises(ValidationError) as e:
                vegeta_settings_type(rate=0, target="https://foo.com/")
            assert e is not None

        # Try setting values via env
        with environment_overrides(
            {
                "SERVO_VEGETA_DURATION": "5m",
                "SERVO_VEGETA_RATE": "0",
                "SERVO_VEGETA_TARGET": "https://opsani.com/",
            }
        ):
            s = vegeta_settings_type()
            assert s.duration == "5m"
            assert s.rate == "0"
            assert s.target == "https://opsani.com/"

        with environment_overrides(
            {
                "SERVO_OTHER_DURATION": "15m",
                "SERVO_OTHER_RATE": "100/1s",
                "SERVO_OTHER_TARGET": "https://opsani.com/servox",
            }
        ):
            s = other_settings_type()
            assert s.duration == "15m"
            assert s.rate == "100/1s"
            assert s.target == "https://opsani.com/servox"


###
### Connector specific
###


class TestVegetaSettings:
    def test_rate_is_required(self) -> None:
        schema = VegetaSettings.schema()
        assert "rate" in schema["required"]

    def test_duration_is_required(self) -> None:
        schema = VegetaSettings.schema()
        assert "duration" in schema["required"]

    def test_validate_infinite_rate(self) -> None:
        s = VegetaSettings(rate="0", duration="0", target="GET http://example.com")
        assert s.rate == "0"

    def test_validate_rate_no_time_unit(self) -> None:
        s = VegetaSettings(rate="500", duration="0", target="GET http://example.com")
        assert s.rate == "500"

    def test_validate_rate_integer(self) -> None:
        s = VegetaSettings(rate=500, duration="0", target="GET http://example.com")
        assert s.rate == "500"

    def test_validate_rate_connections_over_time(self) -> None:
        s = VegetaSettings(rate="500/1s", duration="0", target="GET http://example.com")
        assert s.rate == "500/1s"

    def test_validate_rate_raises_when_invalid(self) -> None:
        with pytest.raises(ValidationError) as e:
            VegetaSettings(
                rate="INVALID", duration="0", target="GET http://example.com"
            )
        assert "1 validation error for VegetaSettings" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("rate",)
        assert (
            e.value.errors()[0]["msg"] == "rate strings are of the form hits/interval"
        )

    def test_validate_rate_raises_when_invalid_duration(self) -> None:
        with pytest.raises(ValidationError) as e:
            VegetaSettings(
                rate="500/1zxzczc", duration="0", target="GET http://example.com"
            )
        assert "1 validation error for VegetaSettings" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("rate",)
        assert (
            e.value.errors()[0]["msg"]
            == "Invalid duration '1zxzczc' in rate '500/1zxzczc'"
        )

    def test_validate_duration_infinite_attack(self) -> None:
        s = VegetaSettings(rate="0", duration="0", target="GET http://example.com")
        assert s.duration == "0"

    def test_validate_duration_seconds(self) -> None:
        s = VegetaSettings(rate="0", duration="1s", target="GET http://example.com")
        assert s.duration == "1s"

    def test_validate_duration_hours_minutes_and_seconds(self) -> None:
        s = VegetaSettings(
            rate="0", duration="1h35m20s", target="GET http://example.com"
        )
        assert s.duration == "1h35m20s"

    def test_validate_duration_invalid(self) -> None:
        with pytest.raises(ValidationError) as e:
            VegetaSettings(
                rate="0", duration="INVALID", target="GET http://example.com"
            )
        assert "1 validation error for VegetaSettings" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("duration",)
        assert e.value.errors()[0]["msg"] == "Invalid duration INVALID"

    def test_validate_target_with_http_format(self) -> None:
        s = VegetaSettings(
            rate="0", duration="0", format="http", target="GET http://example.com"
        )
        assert s.format == TargetFormat.http

    def test_validate_target_with_json_format(self) -> None:
        s = VegetaSettings(
            rate="0",
            duration="0",
            format="json",
            target='{ "url": "http://example.com" }',
        )
        assert s.format == TargetFormat.json

    def test_validate_target_with_invalid_format(self) -> None:
        with pytest.raises(ValidationError) as e:
            VegetaSettings(
                rate="0",
                duration="0",
                format="invalid",
                target="GET http://example.com",
            )
        assert "1 validation error for VegetaSettings" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("format",)
        assert (
            e.value.errors()[0]["msg"]
            == "value is not a valid enumeration member; permitted: 'http', 'json'"
        )

    def test_validate_taget_or_targets_must_be_selected(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError) as e:
            s = VegetaSettings(rate="0", duration="0")
        assert "1 validation error for VegetaSettings" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("__root__",)
        assert e.value.errors()[0]["msg"] == "target or targets must be configured"

    def test_validate_taget_or_targets_cant_both_be_selected(
        self, tmp_path: Path
    ) -> None:
        targets = tmp_path / "targets"
        targets.touch()
        with pytest.raises(ValidationError) as e:
            s = VegetaSettings(
                rate="0",
                duration="0",
                target="GET http://example.com",
                targets="targets",
            )
        assert "1 validation error for VegetaSettings" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("__root__",)
        assert (
            e.value.errors()[0]["msg"] == "target and targets cannot both be configured"
        )

    def test_validate_targets_with_path(self, tmp_path: Path) -> None:
        targets = tmp_path / "targets"
        targets.touch()
        s = VegetaSettings(rate="0", duration="0", targets=targets)
        assert s.targets == targets

    def test_validate_targets_with_path_doesnt_exist(self, tmp_path: Path) -> None:
        targets = tmp_path / "targets"
        with pytest.raises(ValidationError) as e:
            VegetaSettings(rate="0", duration="0", targets=targets)
        assert "2 validation errors for VegetaSettings" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("targets",)
        assert "file or directory at path" in e.value.errors()[0]["msg"]

    def test_providing_invalid_target_with_json_format(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError) as e:
            VegetaSettings(rate="0", duration="0", format="json", target="INVALID")
        assert "1 validation error for VegetaSettings" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("__root__",)
        assert "the target is not valid JSON" in e.value.errors()[0]["msg"]

    def test_providing_invalid_targets_with_json_format(self, tmp_path: Path) -> None:
        targets = tmp_path / "targets.json"
        targets.write_text("<xml>INVALID</xml>")
        with pytest.raises(ValidationError) as e:
            VegetaSettings(rate="0", duration="0", format="json", targets=targets)
        assert "1 validation error for VegetaSettings" in str(e.value)
        assert e.value.errors()[0]["loc"] == ("__root__",)
        assert "the targets file is not valid JSON" in e.value.errors()[0]["msg"]

    # TODO: Test the combination of JSON and HTTP targets


class VegetaConnectorTests:
    pass


def test_init_vegeta_connector() -> None:
    settings = VegetaSettings(
        rate="50/1s", duration="5m", target="GET http://localhost:8080"
    )
    connector = VegetaConnector(settings=settings)
    assert connector is not None


def test_init_vegeta_connector_no_settings() -> None:
    with pytest.raises(ValidationError) as e:
        VegetaConnector(settings=None)
    assert "1 validation error for VegetaConnector" in str(e.value)


def test_init_connector_no_version_raises() -> None:
    class FakeConnector(Connector):
        pass

    with pytest.raises(ValidationError) as e:
        FakeConnector.version = None
        settings = VegetaSettings(
            rate="50/1s", duration="5m", target="GET http://localhost:8080"
        )
        connector = FakeConnector(settings=settings, path="whatever")
    assert e.value.errors()[0]["loc"] == ("__root__",)
    assert e.value.errors()[0]["msg"] == "version must be provided"


def test_init_connector_invalid_version_raises() -> None:
    class FakeConnector(Connector):
        pass

    with pytest.raises(ValidationError) as e:
        FakeConnector.version = "invalid"
        settings = VegetaSettings(
            rate="50/1s", duration="5m", target="GET http://localhost:8080"
        )
        connector = FakeConnector(settings=settings, path="whatever", version="b")
    assert e.value.errors()[0]["loc"] == ("__root__",)
    assert e.value.errors()[0]["msg"] == "invalid is not valid SemVer string"


def test_init_connector_parses_version_string() -> None:
    class FakeConnector(Connector):
        pass

    FakeConnector.version = "0.5.0"
    settings = VegetaSettings(
        rate="50/1s", duration="5m", target="GET http://localhost:8080"
    )
    connector = FakeConnector(settings=settings, path="whatever")
    assert connector.version is not None
    assert connector.version == Version.parse("0.5.0")


def test_init_connector_no_name_raises() -> None:
    class FakeConnector(Connector):
        pass

    with pytest.raises(ValidationError) as e:
        FakeConnector.name = None
        settings = VegetaSettings(
            rate="50/1s", duration="5m", target="GET http://localhost:8080"
        )
        connector = FakeConnector(settings=settings, path="test", name=None)
    assert e.value.errors()[0]["loc"] == ("__root__",)
    assert e.value.errors()[0]["msg"] == "name must be provided"


def test_vegeta_default_key_path() -> None:
    settings = VegetaSettings(
        rate="50/1s", duration="5m", target="GET http://localhost:8080"
    )
    connector = VegetaConnector(settings=settings)
    assert connector.config_key_path == "vegeta"


def test_vegeta_config_override() -> None:
    settings = VegetaSettings(
        rate="50/1s", duration="5m", target="GET http://localhost:8080"
    )
    connector = VegetaConnector(settings=settings, config_key_path="monkey")
    assert connector.config_key_path == "monkey"


def test_vegeta_id_invalid() -> None:
    with pytest.raises(ValidationError) as e:
        settings = VegetaSettings(
            rate="50/1s", duration="5m", target="GET http://localhost:8080"
        )
        connector = VegetaConnector(settings=settings, config_key_path="THIS IS NOT COOL")    
    error_messages = list(map(lambda error: error["msg"], e.value.errors()))
    assert "key paths may only contain alphanumeric characters, hyphens, slashes, periods, and underscores" in error_messages


def test_vegeta_name() -> None:
    assert VegetaConnector.name == "Vegeta Connector"


def test_vegeta_description() -> None:
    assert VegetaConnector.description == "Vegeta load testing connector"


def test_vegeta_version() -> None:
    # TODO: Type violation
    assert VegetaConnector.version == "0.5.0"


def test_vegeta_homepage() -> None:
    # TODO: Type violation
    assert VegetaConnector.homepage == "https://github.com/opsani/vegeta-connector"


def test_vegeta_license() -> None:
    assert VegetaConnector.license == License.APACHE2


def test_vegeta_maturity() -> None:
    assert VegetaConnector.maturity == Maturity.STABLE


## Vegeta CLI tests
def test_vegeta_cli_help(servo_cli: ServoCLI, cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(servo_cli, "--help")
    assert result.exit_code == 0
    assert "Usage: servo [OPTIONS] COMMAND [ARGS]..." in result.stdout


def test_env_variable_prefixing() -> None:
    schema_title_and_description_envs = [
        ["Connector Configuration Schema", "DESCRIPTION",],
        ["Vegeta Connector Configuration Schema", "VEGETA_DESCRIPTION",],
        ["Abstract Servo Configuration Schema", "SERVO_DESCRIPTION",],
    ]
    schemas = [
        ConnectorSettings.schema(),
        VegetaSettings.schema(),
        BaseServoSettings.schema(),
    ]
    # NOTE: popping the env_names without copying is a mistake you will only make once
    values = list(
        map(
            lambda schema: [
                schema["title"],
                schema["properties"]["description"]["env_names"].copy().pop(),
            ],
            schemas,
        )
    )
    assert values == schema_title_and_description_envs


def test_vegeta_cli_schema_json(servo_cli: ServoCLI, cli_runner: CliRunner, optimizer_env: None) -> None:
    result = cli_runner.invoke(servo_cli, "schema vegeta")
    assert result.exit_code == 0    
    schema = json.loads(result.stdout)
    assert schema == {
        'title': 'Vegeta Connector Configuration Schema',
        'description': 'Configuration of the Vegeta connector',
        'type': 'object',
        'properties': {
            'description': {
                'title': 'Description',
                'description': 'An optional annotation describing the configuration.',
                'env_names': [
                    'VEGETA_DESCRIPTION',
                ],
                'type': 'string',
            },
            'rate': {
                'title': 'Rate',
                'description': (
                    'Specifies the request rate per time unit to issue against the targets. Given in the format of req'
                    'uest/time unit.'
                ),
                'env_names': [
                    'VEGETA_RATE',
                ],
                'type': 'string',
            },
            'duration': {
                'title': 'Duration',
                'description': 'Specifies the amount of time to issue requests to the targets.',
                'env_names': [
                    'VEGETA_DURATION',
                ],
                'type': 'string',
            },
            'format': {
                'title': 'Format',
                'description': (
                    'Specifies the format of the targets input. Valid values are http and json. Refer to the Vegeta do'
                    'cs for details.'
                ),
                'default': 'http',
                'env_names': [
                    'VEGETA_FORMAT',
                ],
                'enum': [
                    'http',
                    'json',
                ],
                'type': 'string',
            },
            'target': {
                'title': 'Target',
                'description': (
                    'Specifies a single formatted Vegeta target to load. See the format option to learn about availabl'
                    'e target formats. This option is exclusive of the targets option and will provide a target to Veg'
                    'eta via stdin.'
                ),
                'env_names': [
                    'VEGETA_TARGET',
                ],
                'type': 'string',
            },
            'targets': {
                'title': 'Targets',
                'description': (
                    'Specifies the file from which to read targets. See the format option to learn about available tar'
                    'get formats. This option is exclusive of the target option and will provide targets to via throug'
                    'h a file on disk.'
                ),
                'env_names': [
                    'VEGETA_TARGETS',
                ],
                'type': 'string',
                'format': 'file-path',
            },
            'connections': {
                'title': 'Connections',
                'description': 'Specifies the maximum number of idle open connections per target host.',
                'default': 10000,
                'env_names': [
                    'VEGETA_CONNECTIONS',
                ],
                'type': 'integer',
            },
            'workers': {
                'title': 'Workers',
                'description': (
                    'Specifies the initial number of workers used in the attack. The workers will automatically increa'
                    'se to achieve the target request rate, up to max-workers.'
                ),
                'default': 10,
                'env_names': [
                    'VEGETA_WORKERS',
                ],
                'type': 'integer',
            },
            'max_workers': {
                'title': 'Max Workers',
                'description': (
                    'The maximum number of workers used to sustain the attack. This can be used to control the concurr'
                    'ency of the attack to simulate a target number of clients.'
                ),
                'default': 18446744073709551615,
                'env_names': [
                    'VEGETA_MAX_WORKERS',
                ],
                'type': 'integer',
            },
            'max_body': {
                'title': 'Max Body',
                'description': (
                    'Specifies the maximum number of bytes to capture from the body of each response. Remaining unread'
                    ' bytes will be fully read but discarded.'
                ),
                'default': -1,
                'env_names': [
                    'VEGETA_MAX_BODY',
                ],
                'type': 'integer',
            },
            'http2': {
                'title': 'Http2',
                'description': 'Specifies whether to enable HTTP/2 requests to servers which support it.',
                'default': True,
                'env_names': [
                    'VEGETA_HTTP2',
                ],
                'type': 'boolean',
            },
            'keepalive': {
                'title': 'Keepalive',
                'description': 'Specifies whether to reuse TCP connections between HTTP requests.',
                'default': True,
                'env_names': [
                    'VEGETA_KEEPALIVE',
                ],
                'type': 'boolean',
            },
            'insecure': {
                'title': 'Insecure',
                'description': 'Specifies whether to ignore invalid server TLS certificates.',
                'default': False,
                'env_names': [
                    'VEGETA_INSECURE',
                ],
                'type': 'boolean',
            },
        },
        'required': [
            'rate',
            'duration',
        ],
        'additionalProperties': False,
    }


@pytest.mark.xfail
def test_vegeta_cli_schema_text(servo_cli: ServoCLI, cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(servo_cli, "schema -f text")
    assert result.exit_code == 2
    assert "not yet implemented" in result.stderr


@pytest.mark.xfail
def test_vegeta_cli_schema_html(servo_cli: ServoCLI, cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(servo_cli, "schema -f html")
    assert result.exit_code == 2
    assert "not yet implemented" in result.stderr


def test_vegeta_cli_generate(
    tmp_path: Path, servo_cli: ServoCLI, cli_runner: CliRunner
) -> None:
    result = cli_runner.invoke(servo_cli, "generate vegeta")
    assert result.exit_code == 0
    assert "Generated servo.yaml" in result.stdout
    config_file = tmp_path / "servo.yaml"
    config = yaml.full_load(config_file.read_text())
    assert config == {
        'connectors': ['vegeta'],
        'vegeta': {
            'description': 'Update the rate, duration, and target/targets to match your load profile',
            'duration': '5m',
            'rate': '50/1s',
            'target': 'https://example.com/',
        },
    }

def test_vegeta_cli_generate_filename(
    tmp_path: Path, servo_cli: ServoCLI, cli_runner: CliRunner
) -> None:
    result = cli_runner.invoke(servo_cli, "generate vegeta -f vegeta.yaml")
    assert result.exit_code == 0
    assert "Generated vegeta.yaml" in result.stdout
    config_file = tmp_path / "vegeta.yaml"
    config = yaml.full_load(config_file.read_text())
    assert config == {
        'connectors': ['vegeta'],
        'vegeta': {
            'description': 'Update the rate, duration, and target/targets to match your load profile',
            'duration': '5m',
            'rate': '50/1s',
            'target': 'https://example.com/',
        },
    }

def test_vegeta_cli_generate_quiet(
    tmp_path: Path, servo_cli: ServoCLI, cli_runner: CliRunner
) -> None:
    result = cli_runner.invoke(servo_cli, "generate vegeta -f vegeta.yaml --quiet")
    assert result.exit_code == 0
    assert result.stdout == ""
    config_file = tmp_path / "vegeta.yaml"
    config = yaml.full_load(config_file.read_text())
    assert config == {
        'connectors': ['vegeta'],
        'vegeta': {
            'description': 'Update the rate, duration, and target/targets to match your load profile',
            'duration': '5m',
            'rate': '50/1s',
            'target': 'https://example.com/',
        },
    }

def test_vegeta_cli_generate_standalone(
    tmp_path: Path, servo_cli: ServoCLI, cli_runner: CliRunner
) -> None:
    result = cli_runner.invoke(servo_cli, "generate vegeta -f vegeta.yaml --standalone")
    assert result.exit_code == 0
    config_file = tmp_path / "vegeta.yaml"
    config = yaml.full_load(config_file.read_text())
    assert config == {
        'vegeta': {
            'description': 'Update the rate, duration, and target/targets to match your load profile',
            'duration': '5m',
            'rate': '50/1s',
            'target': 'https://example.com/',
        },
    }

def test_vegeta_cli_generate_aliases(
    tmp_path: Path, servo_cli: ServoCLI, cli_runner: CliRunner
) -> None:
    result = cli_runner.invoke(servo_cli, "generate one:vegeta two:vegeta -f vegeta.yaml")
    assert result.exit_code == 0
    config_file = tmp_path / "vegeta.yaml"
    config = yaml.full_load(config_file.read_text())
    assert config == {
        'connectors': {
            'one': 'vegeta',
            'two': 'vegeta',
        },
        'one': {
            'description': 'Update the rate, duration, and target/targets to match your load profile',
            'duration': '5m',
            'rate': '50/1s',
            'target': 'https://example.com/',
        },
        'two': {
            'description': 'Update the rate, duration, and target/targets to match your load profile',
            'duration': '5m',
            'rate': '50/1s',
            'target': 'https://example.com/',
        },
    }

def test_vegeta_cli_generate_with_defaults(
    tmp_path: Path, servo_cli: ServoCLI, cli_runner: CliRunner
) -> None:
    result = cli_runner.invoke(servo_cli, "generate vegeta --defaults -f vegeta.yaml -s")
    assert result.exit_code == 0
    assert "Generated vegeta.yaml" in result.stdout
    config_file = tmp_path / "vegeta.yaml"
    config = yaml.full_load(config_file.read_text())
    assert config == {
        'description': None,
        'vegeta': {
            'connections': 10000,
            'description': 'Update the rate, duration, and target/targets to match your load profile',
            'duration': '5m',
            'format': 'http',
            'http2': True,
            'insecure': False,
            'keepalive': True,
            'max_body': -1,
            'max_workers': 18446744073709551615,
            'rate': '50/1s',
            'target': 'https://example.com/',
            'targets': None,
            'workers': 10,
        },
    }


def test_vegeta_cli_validate(
    tmp_path: Path, servo_cli: ServoCLI, cli_runner: CliRunner
) -> None:
    config_file = tmp_path / "vegeta.yaml"
    config_file.write_text(
        (
            "connections: 10000\n"
            "description: null\n"
            "duration: 5m\n"
            "format: http\n"
            "http2: true\n"
            "insecure: false\n"
            "keepalive: true\n"
            "max_body: -1\n"
            "max_workers: 18446744073709551615\n"
            "rate: 50/1s\n"
            "target: GET http://localhost:8080\n"
            "targets: null\n"
            "workers: 10\n"
        )
    )
    result = cli_runner.invoke(servo_cli, "validate vegeta.yaml")
    assert result.exit_code == 0
    assert "√ Valid Vegeta Connector configuration in vegeta.yaml" in result.stdout


def test_vegeta_cli_validate_no_such_file(
    tmp_path: Path, servo_cli: ServoCLI, cli_runner: CliRunner
) -> None:
    result = cli_runner.invoke(servo_cli, "validate doesntexist.yaml")
    assert result.exit_code == 2
    assert (
        "Error: Invalid value for '[FILE]': File 'doesntexist.yaml' does not exist.\n"
        in result.stderr
    )


def test_vegeta_cli_validate_invalid_config(
    tmp_path: Path, servo_cli: ServoCLI, cli_runner: CliRunner
) -> None:
    config_file = tmp_path / "invalid.yaml"
    config_file.write_text(
        (
            "connections: 10000\n"
            "description: null\n"
            # 'duration: 5m\n'  # Duration is omitted
            "format: http\n"
            "http2: true\n"
            "insecure: false\n"
            "keepalive: true\n"
            "max_body: -1\n"
            "max_workers: 18446744073709551615\n"
            #'rate: 50/1s\n'  # Rate is omitted
            "target: GET http://localhost:8080\n"
            "targets: null\n"
            "workers: 10\n"
        )
    )
    result = cli_runner.invoke(
        servo_cli, "validate -f invalid.yaml", catch_exceptions=False
    )
    debug(result.stdout, result.stderr)
    assert result.exit_code == 1
    assert "2 validation errors for VegetaSettings" in result.stderr


def test_vegeta_cli_validate_invalid_syntax(
    tmp_path: Path, servo_cli: ServoCLI, cli_runner: CliRunner
) -> None:
    config_file = tmp_path / "invalid.yaml"
    config_file.write_text(
        ("connections: 10000\n" "descriptions\n\n null\n" "duratio\n\n_   n: 5m\n")
    )
    result = cli_runner.invoke(
        servo_cli, "validate invalid.yaml", catch_exceptions=False
    )
    assert result.exit_code == 1
    assert "X Invalid Vegeta Connector configuration in invalid.yaml\n" in result.stdout
    assert "could not find expected ':'" in result.stderr


def test_vegeta_cli_version(vegeta_cli: typer.Typer, cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(vegeta_cli, "version")
    assert result.exit_code == 0
    assert (
        "Vegeta Connector v0.5.0 (Stable)\n"
        "Vegeta load testing connector\n"
        "https://github.com/opsani/vegeta-connector\n"
        "Licensed under the terms of Apache 2.0\n"
    ) in result.stdout


def test_vegeta_cli_version_short(
    vegeta_cli: typer.Typer, cli_runner: CliRunner
) -> None:
    result = cli_runner.invoke(vegeta_cli, "version -s")
    assert result.exit_code == 0
    assert "Vegeta Connector v0.5.0" in result.stdout


def test_vegeta_cli_loadgen(vegeta_cli: typer.Typer, cli_runner: CliRunner) -> None:
    pass


class TestConnectorEvents:
    class FakeConnector(Connector):
        @event()
        def example_event(self) -> None:
            return 12345

    class AnotherFakeConnector(FakeConnector):
        @event()
        def another_example_event(self) -> str:
            return "example_event"

    def test_event_registration(self) -> None:
        events = TestConnectorEvents.FakeConnector.__events__
        assert events is not None
        event = events["example_event"]
        assert event is not None

    def test_event_inheritance(self) -> None:
        events = TestConnectorEvents.AnotherFakeConnector.__events__
        assert events is not None
        event = events["example_event"]
        assert event is not None

    def test_responds_to_event(self) -> None:
        assert TestConnectorEvents.FakeConnector.responds_to_event("example_event")
        assert not TestConnectorEvents.FakeConnector.responds_to_event(
            "another_example_event"
        )

    def test_responds_to_event_subclassing(self) -> None:
        assert TestConnectorEvents.AnotherFakeConnector.responds_to_event(
            "example_event"
        )
        assert TestConnectorEvents.AnotherFakeConnector.responds_to_event(
            "another_example_event"
        )

    def test_event_invoke(self) -> None:
        settings = ConnectorSettings.construct()
        connector = TestConnectorEvents.FakeConnector(settings=settings)
        result = connector.process_event("example_event")
        assert result is not None
        assert result.event == "example_event"
        assert result.connector == connector
        assert result.value == 12345

    def test_event_invoke_not_supported(self) -> None:
        settings = ConnectorSettings.construct()
        connector = TestConnectorEvents.FakeConnector(settings=settings)
        result = connector.process_event("unknown_event")
        assert result is None


# def test_loading_optimizer_from_environment() -> None:
#     with environment_overrides({
#         'SERVO_OPTIMIZER': 'dev.opsani.com/servox',
#         'SERVO_TOKEN': '123456789',
#     }):
#         # assert os.environ['SERVO_OPTIMIZER'] is not None
#         # o = Optimizer()
#         # assert s.connectors == {'measure': 'servo.connector.MeasureConnector'}
#         # result = cli_runner.invoke(servo_cli, "version")
#         # assert result.exit_code == 0
#         # assert "Vegeta Connector v0.5.0" in result.stdout
