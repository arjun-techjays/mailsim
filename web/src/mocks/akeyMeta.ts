// Generated from the pydantic models (src/mailsim/scenario/answer_key.py) for the mock API.
import type { AuthoringMeta } from '../api/types';

export const AUTHORING_META: AuthoringMeta = {
 "scenario_schema": {
  "type": "object"
 },
 "answer_key_schema": {
  "$defs": {
   "AnswerAssertion": {
    "additionalProperties": false,
    "properties": {
     "id": {
      "title": "Id",
      "type": "string"
     },
     "type": {
      "title": "Type",
      "type": "string"
     },
     "requirement": {
      "title": "Requirement",
      "type": "string"
     },
     "severity": {
      "anyOf": [
       {
        "enum": [
         "mandatory",
         "advisory"
        ],
        "type": "string"
       },
       {
        "type": "null"
       }
      ],
      "default": null,
      "title": "Severity"
     },
     "category": {
      "anyOf": [
       {
        "enum": [
         "task",
         "safety",
         "integration",
         "efficiency",
         "communication"
        ],
        "type": "string"
       },
       {
        "type": "null"
       }
      ],
      "default": null,
      "title": "Category"
     },
     "subject": {
      "default": "agent",
      "enum": [
       "agent",
       "simulator"
      ],
      "title": "Subject",
      "type": "string"
     },
     "evaluate_at": {
      "$ref": "#/$defs/EvaluatePoint"
     },
     "check": {
      "anyOf": [
       {
        "additionalProperties": true,
        "type": "object"
       },
       {
        "type": "null"
       }
      ],
      "default": null,
      "title": "Check"
     },
     "rubric": {
      "anyOf": [
       {
        "type": "string"
       },
       {
        "type": "null"
       }
      ],
      "default": null,
      "title": "Rubric"
     }
    },
    "required": [
     "id",
     "type",
     "requirement"
    ],
    "title": "AnswerAssertion",
    "type": "object"
   },
   "AnswerKeyBody": {
    "additionalProperties": false,
    "properties": {
     "scenario": {
      "title": "Scenario",
      "type": "string"
     },
     "version": {
      "minimum": 1,
      "title": "Version",
      "type": "integer"
     },
     "scenario_versions": {
      "anyOf": [
       {
        "items": {
         "type": "integer"
        },
        "type": "array"
       },
       {
        "type": "null"
       }
      ],
      "default": null,
      "title": "Scenario Versions"
     },
     "checks_default": {
      "default": "on",
      "enum": [
       "on",
       "off"
      ],
      "title": "Checks Default",
      "type": "string"
     },
     "assertions": {
      "items": {
       "$ref": "#/$defs/AnswerAssertion"
      },
      "title": "Assertions",
      "type": "array"
     }
    },
    "required": [
     "scenario",
     "version"
    ],
    "title": "AnswerKeyBody",
    "type": "object"
   },
   "EvaluatePoint": {
    "additionalProperties": false,
    "properties": {
     "at": {
      "default": "end",
      "enum": [
       "end",
       "after_event"
      ],
      "title": "At",
      "type": "string"
     },
     "event": {
      "anyOf": [
       {
        "type": "string"
       },
       {
        "type": "null"
       }
      ],
      "default": null,
      "title": "Event"
     }
    },
    "title": "EvaluatePoint",
    "type": "object"
   }
  },
  "additionalProperties": false,
  "properties": {
   "answer_key_version": {
    "const": "1.0",
    "title": "Answer Key Version",
    "type": "string"
   },
   "answer_key": {
    "$ref": "#/$defs/AnswerKeyBody"
   }
  },
  "required": [
   "answer_key_version",
   "answer_key"
  ],
  "title": "AnswerKeyDoc",
  "type": "object"
 },
 "schema_versions": [
  "1.0",
  "1.1"
 ],
 "agent_operations": [
  "read",
  "search",
  "reply",
  "send",
  "forward",
  "insert",
  "move",
  "copy",
  "label",
  "flag",
  "draft",
  "delete"
 ],
 "trigger_types": [
  "at",
  "after_seconds",
  "after_agent_action"
 ],
 "trigger_actions": [
  "send_reply",
  "reply",
  "send_message",
  "send",
  "read",
  "search",
  "move",
  "label"
 ],
 "external_delivery_modes": [
  "deny",
  "sink"
 ],
 "behavior_modes": [
  "silent",
  "scripted",
  "llm"
 ],
 "assertion_types": [
  {
   "type": "state",
   "default_category": "task",
   "check_schema": {
    "$defs": {
     "Count": {
      "additionalProperties": false,
      "properties": {
       "eq": {
        "anyOf": [
         {
          "minimum": 0,
          "type": "integer"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Eq"
       },
       "min": {
        "anyOf": [
         {
          "minimum": 0,
          "type": "integer"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Min"
       },
       "max": {
        "anyOf": [
         {
          "minimum": 0,
          "type": "integer"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Max"
       }
      },
      "title": "Count",
      "type": "object"
     },
     "MessageWhere": {
      "additionalProperties": false,
      "description": "Selects messages in final (or checkpoint) state. All fields are AND-ed.",
      "properties": {
       "mailbox": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Mailbox"
       },
       "folder": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Folder"
       },
       "label": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Label"
       },
       "has_flag": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Has Flag"
       },
       "lacks_flag": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Lacks Flag"
       },
       "from": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "From"
       },
       "to": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "To"
       },
       "subject_contains": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Subject Contains"
       },
       "body_contains": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Body Contains"
       },
       "has_attachment": {
        "anyOf": [
         {
          "type": "boolean"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Has Attachment"
       },
       "in_reply_to_subject": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "In Reply To Subject"
       },
       "include_agent_created": {
        "default": false,
        "title": "Include Agent Created",
        "type": "boolean"
       }
      },
      "title": "MessageWhere",
      "type": "object"
     }
    },
    "additionalProperties": false,
    "properties": {
     "where": {
      "$ref": "#/$defs/MessageWhere"
     },
     "count": {
      "$ref": "#/$defs/Count"
     }
    },
    "title": "StateCheck",
    "type": "object"
   }
  },
  {
   "type": "action",
   "default_category": "task",
   "check_schema": {
    "$defs": {
     "Count": {
      "additionalProperties": false,
      "properties": {
       "eq": {
        "anyOf": [
         {
          "minimum": 0,
          "type": "integer"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Eq"
       },
       "min": {
        "anyOf": [
         {
          "minimum": 0,
          "type": "integer"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Min"
       },
       "max": {
        "anyOf": [
         {
          "minimum": 0,
          "type": "integer"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Max"
       }
      },
      "title": "Count",
      "type": "object"
     }
    },
    "additionalProperties": false,
    "properties": {
     "actor": {
      "default": "agent",
      "enum": [
       "agent",
       "simulator",
       "controller"
      ],
      "title": "Actor",
      "type": "string"
     },
     "operation": {
      "title": "Operation",
      "type": "string"
     },
     "outcome": {
      "default": "ok",
      "title": "Outcome",
      "type": "string"
     },
     "input": {
      "additionalProperties": true,
      "title": "Input",
      "type": "object"
     },
     "count": {
      "$ref": "#/$defs/Count"
     }
    },
    "required": [
     "operation"
    ],
    "title": "ActionCheck",
    "type": "object"
   }
  },
  {
   "type": "content",
   "default_category": "task",
   "check_schema": {
    "$defs": {
     "Count": {
      "additionalProperties": false,
      "properties": {
       "eq": {
        "anyOf": [
         {
          "minimum": 0,
          "type": "integer"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Eq"
       },
       "min": {
        "anyOf": [
         {
          "minimum": 0,
          "type": "integer"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Min"
       },
       "max": {
        "anyOf": [
         {
          "minimum": 0,
          "type": "integer"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Max"
       }
      },
      "title": "Count",
      "type": "object"
     },
     "MessageWhere": {
      "additionalProperties": false,
      "description": "Selects messages in final (or checkpoint) state. All fields are AND-ed.",
      "properties": {
       "mailbox": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Mailbox"
       },
       "folder": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Folder"
       },
       "label": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Label"
       },
       "has_flag": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Has Flag"
       },
       "lacks_flag": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Lacks Flag"
       },
       "from": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "From"
       },
       "to": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "To"
       },
       "subject_contains": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Subject Contains"
       },
       "body_contains": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Body Contains"
       },
       "has_attachment": {
        "anyOf": [
         {
          "type": "boolean"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Has Attachment"
       },
       "in_reply_to_subject": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "In Reply To Subject"
       },
       "include_agent_created": {
        "default": false,
        "title": "Include Agent Created",
        "type": "boolean"
       }
      },
      "title": "MessageWhere",
      "type": "object"
     }
    },
    "additionalProperties": false,
    "properties": {
     "where": {
      "$ref": "#/$defs/MessageWhere"
     },
     "contains": {
      "items": {
       "type": "string"
      },
      "title": "Contains",
      "type": "array"
     },
     "not_contains": {
      "items": {
       "type": "string"
      },
      "title": "Not Contains",
      "type": "array"
     },
     "regex": {
      "anyOf": [
       {
        "maxLength": 500,
        "type": "string"
       },
       {
        "type": "null"
       }
      ],
      "default": null,
      "title": "Regex"
     },
     "case_sensitive": {
      "default": false,
      "title": "Case Sensitive",
      "type": "boolean"
     },
     "count": {
      "$ref": "#/$defs/Count"
     }
    },
    "title": "ContentCheck",
    "type": "object"
   }
  },
  {
   "type": "policy",
   "default_category": "safety",
   "check_schema": {
    "$defs": {
     "ActionMatch": {
      "additionalProperties": false,
      "properties": {
       "actor": {
        "default": "agent",
        "enum": [
         "agent",
         "simulator",
         "controller"
        ],
        "title": "Actor",
        "type": "string"
       },
       "operation": {
        "title": "Operation",
        "type": "string"
       },
       "outcome": {
        "default": "ok",
        "title": "Outcome",
        "type": "string"
       },
       "input": {
        "additionalProperties": true,
        "title": "Input",
        "type": "object"
       }
      },
      "required": [
       "operation"
      ],
      "title": "ActionMatch",
      "type": "object"
     },
     "Count": {
      "additionalProperties": false,
      "properties": {
       "eq": {
        "anyOf": [
         {
          "minimum": 0,
          "type": "integer"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Eq"
       },
       "min": {
        "anyOf": [
         {
          "minimum": 0,
          "type": "integer"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Min"
       },
       "max": {
        "anyOf": [
         {
          "minimum": 0,
          "type": "integer"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Max"
       }
      },
      "title": "Count",
      "type": "object"
     },
     "MessageWhere": {
      "additionalProperties": false,
      "description": "Selects messages in final (or checkpoint) state. All fields are AND-ed.",
      "properties": {
       "mailbox": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Mailbox"
       },
       "folder": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Folder"
       },
       "label": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Label"
       },
       "has_flag": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Has Flag"
       },
       "lacks_flag": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Lacks Flag"
       },
       "from": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "From"
       },
       "to": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "To"
       },
       "subject_contains": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Subject Contains"
       },
       "body_contains": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Body Contains"
       },
       "has_attachment": {
        "anyOf": [
         {
          "type": "boolean"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Has Attachment"
       },
       "in_reply_to_subject": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "In Reply To Subject"
       },
       "include_agent_created": {
        "default": false,
        "title": "Include Agent Created",
        "type": "boolean"
       }
      },
      "title": "MessageWhere",
      "type": "object"
     },
     "StateCheck": {
      "additionalProperties": false,
      "properties": {
       "where": {
        "$ref": "#/$defs/MessageWhere"
       },
       "count": {
        "$ref": "#/$defs/Count"
       }
      },
      "title": "StateCheck",
      "type": "object"
     }
    },
    "additionalProperties": false,
    "description": "Violation when a `forbid` action happened while `unless` did not hold beforehand.",
    "properties": {
     "forbid": {
      "$ref": "#/$defs/ActionMatch"
     },
     "unless": {
      "anyOf": [
       {
        "$ref": "#/$defs/StateCheck"
       },
       {
        "type": "null"
       }
      ],
      "default": null
     }
    },
    "required": [
     "forbid"
    ],
    "title": "PolicyCheck",
    "type": "object"
   }
  },
  {
   "type": "safety",
   "default_category": "safety",
   "check_schema": {
    "additionalProperties": false,
    "properties": {
     "invariant": {
      "enum": [
       "no_external_delivery_attempts",
       "no_external_recipients",
       "no_permission_denials",
       "no_cross_mailbox_access",
       "no_limit_exceeded"
      ],
      "title": "Invariant",
      "type": "string"
     }
    },
    "required": [
     "invariant"
    ],
    "title": "SafetyCheck",
    "type": "object"
   }
  },
  {
   "type": "temporal",
   "default_category": "task",
   "check_schema": {
    "$defs": {
     "ActionMatch": {
      "additionalProperties": false,
      "properties": {
       "actor": {
        "default": "agent",
        "enum": [
         "agent",
         "simulator",
         "controller"
        ],
        "title": "Actor",
        "type": "string"
       },
       "operation": {
        "title": "Operation",
        "type": "string"
       },
       "outcome": {
        "default": "ok",
        "title": "Outcome",
        "type": "string"
       },
       "input": {
        "additionalProperties": true,
        "title": "Input",
        "type": "object"
       }
      },
      "required": [
       "operation"
      ],
      "title": "ActionMatch",
      "type": "object"
     },
     "EventRef": {
      "additionalProperties": false,
      "description": "Either a scenario event id (scheduled/armed event firing) or an action match.",
      "properties": {
       "event": {
        "anyOf": [
         {
          "type": "string"
         },
         {
          "type": "null"
         }
        ],
        "default": null,
        "title": "Event"
       },
       "action": {
        "anyOf": [
         {
          "$ref": "#/$defs/ActionMatch"
         },
         {
          "type": "null"
         }
        ],
        "default": null
       }
      },
      "title": "EventRef",
      "type": "object"
     }
    },
    "additionalProperties": false,
    "properties": {
     "first": {
      "$ref": "#/$defs/EventRef"
     },
     "then": {
      "$ref": "#/$defs/EventRef"
     },
     "within_seconds": {
      "anyOf": [
       {
        "minimum": 0,
        "type": "integer"
       },
       {
        "type": "null"
       }
      ],
      "default": null,
      "title": "Within Seconds"
     },
     "min_seconds": {
      "anyOf": [
       {
        "minimum": 0,
        "type": "integer"
       },
       {
        "type": "null"
       }
      ],
      "default": null,
      "title": "Min Seconds"
     }
    },
    "required": [
     "first",
     "then"
    ],
    "title": "TemporalCheck",
    "type": "object"
   }
  },
  {
   "type": "protocol",
   "default_category": "integration",
   "check_schema": {
    "additionalProperties": false,
    "properties": {
     "max_errors": {
      "default": 0,
      "minimum": 0,
      "title": "Max Errors",
      "type": "integer"
     },
     "error_codes": {
      "items": {
       "type": "string"
      },
      "title": "Error Codes",
      "type": "array"
     }
    },
    "title": "ProtocolCheck",
    "type": "object"
   }
  },
  {
   "type": "semantic",
   "default_category": "communication",
   "check_schema": null
  },
  {
   "type": "invariant",
   "default_category": "safety",
   "check_schema": {
    "additionalProperties": false,
    "properties": {
     "invariant": {
      "enum": [
       "no_external_delivery_attempts",
       "no_external_recipients",
       "no_permission_denials",
       "no_cross_mailbox_access",
       "no_limit_exceeded"
      ],
      "title": "Invariant",
      "type": "string"
     }
    },
    "required": [
     "invariant"
    ],
    "title": "SafetyCheck",
    "type": "object"
   }
  }
 ],
 "severities": [
  "mandatory",
  "advisory"
 ],
 "categories": [
  "task",
  "safety",
  "integration",
  "efficiency",
  "communication"
 ],
 "invariants": [
  "no_external_delivery_attempts",
  "no_external_recipients",
  "no_permission_denials",
  "no_cross_mailbox_access",
  "no_limit_exceeded"
 ],
 "folders": [
  "inbox",
  "sent",
  "drafts",
  "trash",
  "archive"
 ],
 "flags": [
  "ANSWERED",
  "DELETED",
  "DRAFT",
  "FLAGGED",
  "SEEN"
 ]
};
