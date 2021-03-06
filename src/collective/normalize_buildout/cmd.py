# -*- coding: utf-8 -*-
from StringIO import StringIO
from argparse import ArgumentParser
from collections import defaultdict
import fileinput
import logging
import re
import sys


is_section = re.compile(r'^\[(.*)\]')
is_comment = re.compile(r'^#.*')
is_option = re.compile(r'^(\S+) *[+-=]')
is_nextline_option = re.compile(r'^    ')


logger = logging.getLogger(__name__)
logging.basicConfig()


def parse(stream):
    sections = {'BEFORE_BUILDOUT': {'options': [], 'comments': []}}
    state = 'OPTION/SECTION'
    current_section = sections['BEFORE_BUILDOUT']
    next_comment = []
    current_option = None
    for line in stream:
        if state == 'OPTION/SECTION':
            if is_section.match(line):
                new_section_name = is_section.findall(line)[0]
                assert new_section_name not in sections
                sections[new_section_name] = {'options': [], 'comments': []}
                current_section = sections[new_section_name]
                current_section['comments'] = next_comment
                next_comment = []
            elif is_comment.match(line):
                next_comment.append(line)
            elif is_option.match(line):
                name = is_option.findall(line)[0]
                current_option = {'lines': [line], 'comments': next_comment,
                                  'name': name}
                current_section['options'].append(current_option)
                next_comment = []
            elif is_nextline_option.match(line):
                state = 'MULTILINE_OPTION'
                current_option['lines'].extend(next_comment)
                next_comment = []
                current_option['lines'].append(line)
            elif line == '\n':
                continue
            else:
                raise Exception("Did not understand this line: %s",
                                line)
        elif state == 'MULTILINE_OPTION':
            if is_section.match(line):
                new_section_name = is_section.findall(line)[0]
                assert new_section_name not in sections
                sections[new_section_name] = {'options': [], 'comments': []}
                current_section = sections[new_section_name]
                current_section['comments'] = next_comment
                next_comment = []
                state = 'OPTION/SECTION'
            elif is_comment.match(line):
                current_option['lines'].append(line)
            elif is_option.match(line):
                state = 'OPTION/SECTION'
                name = is_option.findall(line)[0]
                current_option = {'lines': [line], 'comments': next_comment,
                                  'name': name}
                current_section['options'].append(current_option)
                next_comment = []
            elif is_nextline_option.match(line):
                current_option['lines'].append(line)
            elif line == '\n':
                state = 'OPTION/SECTION'
            else:
                raise Exception("Did not understand this line: %s",
                                line)

    return sections


def simple_option_handler(option, stream):
    for line in option['comments'] + option['lines']:
        stream.write(line)


def sorted_option_handler(option, stream):
    sortable = option['lines'][1:]
    sortable.sort()
    for line in option['comments'] + option['lines'][0:1]:
        stream.write(line)
    for line in sortable:
        stream.write(line)


option_handlers = defaultdict(lambda: simple_option_handler)
option_handlers['eggs'] = sorted_option_handler
option_handlers['zcml'] = sorted_option_handler
option_handlers['auto-checkout'] = sorted_option_handler


def stream_sorted_options(options, stream):
    def remove_option(name):
        options_to_remove = filter(lambda option: option['name'] == name,
                                   options)
        if options_to_remove:
            options.remove(options_to_remove[0])
            return options_to_remove[0]
        return None
    first_option = remove_option('recipe')

    options.sort(key=lambda option: option['lines'][0])

    for option in [first_option] + options:
        if not option:
            continue
        option_handlers[option['name']](option, stream)


def sources_section_handler(options, stream):
    options.sort(key=lambda x: x['lines'][0])
    longest_name = 0
    longest_repo_type = 0
    longest_url = 0
    longest_args = {}
    all_args = set()
    for option in options:
        name, rest = map(str.strip, option['lines'][0].split('=', 1))
        try:
            repo_type, url, rest = map(str.strip, rest.split(' ', 2))
        except ValueError:
            repo_type, url = map(str.strip, rest.split(' ', 1))
            rest = ''
        args = dict((arg.split('=') for arg in
                     map(str.strip, rest.split(' ')) if arg))
        longest_name = max(longest_name, len(name))
        longest_repo_type = max(longest_repo_type, len(repo_type))
        longest_url = max(longest_url, len(url))
        for arg, arg_value in args.items():
            all_args.add(arg)
            longest_args[arg] = max(longest_args.get(arg, 0),
                                    len(arg_value))
        option['name'] = name
        option['repo_type'] = repo_type
        option['url'] = url
        for arg, arg_value in args.items():
            option['arg_{0}'.format(arg)] = arg_value

    all_args = sorted(all_args)
    if 'branch' in all_args:
        all_args.remove('branch')
    for option in options:
        if 'branch' in longest_args:
            arg_string = ''.join(('{arg}={{entry[arg_{arg}]:{len}}} '
                                  .format(arg=arg, len=longest_args[arg])
                                  if 'arg_{0}'.format(arg) in option
                                  else ' ' * (longest_args[arg] + 2 + len(arg))
                                  for arg in ['branch']))
        else:
            arg_string = ''
        arg_string += ''.join(('{arg}={{entry[arg_{arg}]}} '
                               .format(arg=arg)
                               if 'arg_{0}'.format(arg) in option
                               else ''
                               for arg in all_args))
        format_string = ('{comments}{{entry[name]:{longest_name}}} = {{entry[repo_type]:{longest_repo_type}}} {{entry[url]:{longest_url}}} {args:s}\n'  # NOQA
                         .format(args=arg_string,
                                 longest_name=longest_name,
                                 longest_repo_type=longest_repo_type,
                                 longest_url=longest_url,
                                 comments=''.join(option['comments'])))
        stream.write(format_string.format(entry=option).strip() + '\n')


section_handlers = defaultdict(lambda: stream_sorted_options)
section_handlers['sources'] = sources_section_handler


def stream_sorted_sections(sections, stream):
    section_keys = sections.keys()
    for special_key in ['buildout', 'versions', 'BEFORE_BUILDOUT']:
        if special_key in section_keys:
            section_keys.remove(special_key)
    section_keys.sort()
    section_keys = (['BEFORE_BUILDOUT', 'buildout'] + section_keys
                    + ['versions'])
    section_keys = filter(lambda key: sections.get(key, None), section_keys)
    for section_key in section_keys:
        section = sections[section_key]
        if section_key != 'BEFORE_BUILDOUT':
            if section_key != section_keys[1]:
                stream.write('\n')
            stream.write('[{0}]\n'.format(section_key))
        stream.write(''.join(section['comments']))
        section_handlers[section_key](section['options'], stream)


def sort(instream, outstream):
    sections = parse(instream)
    stream_sorted_sections(sections, outstream)


def cmd():
    parser = ArgumentParser()
    parser.add_argument(
        "-c",
        "--check",
        dest="check",
        action="store_true",
        default=False,
        help=("Do not modify buildout file, "
              "only return if file would be modified"))
    parser.add_argument("configfile",
                        help=('The configfile to normalize in place, '
                              'or "-" to read the config file from stdin '
                              'and return the result to stdout'))
    args = parser.parse_args()

    if args.configfile == '-':
        instream = StringIO()
        instream.write(sys.stdin.read())
        instream.seek(0)
        pipe = True
    else:
        instream = open(args.configfile)
        pipe = False
    outstream = StringIO()
    try:
        sort(instream, outstream)
    except Exception:
        logger.exception('Could not parse file')
        return sys.exit(1)
    else:
        instream.seek(0)
        outstream.seek(0)
        changed = instream.read() != outstream.read()
        outstream.seek(0)
        if not changed:
            if pipe:
                sys.stdout.write(outstream.read())
            sys.exit(0)
        else:
            if args.check:
                print("File is not normalized")
                sys.exit(1)
            else:
                if pipe:
                    sys.stdout.write(outstream.read())
                else:
                    file(args.configfile, 'w').write(outstream.read())


if __name__ == '__main__':
    sys.exit(sort(fileinput.input()))
